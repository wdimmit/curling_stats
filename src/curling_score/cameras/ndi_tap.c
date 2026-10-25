/* libavcodec-ndi.so.61: the packet tap, loaded by libndi as its decoder.
 *
 * libndi (6.3.2, bundled in the cyndilib 0.1.3 wheel) decodes NDI HX by
 * dlopen-ing libavcodec-ndi.so.61, or failing that libavcodec.so.61, and
 * looking up the avcodec_* functions it needs in whichever it found. This
 * library is that first name. It is linked with NEEDED libavcodec.so.61, so
 * every lookup it does not answer itself falls through to FFmpeg 7. It
 * answers three:
 *
 * - avcodec_alloc_context3 remembers the context made for AV_CODEC_ID_H264.
 *   That tells the video from the AAC context libndi also makes on the PTZ
 *   cameras. It keeps only the latest, so run one receiver per process.
 * - avcodec_send_packet on that context passes the packet on to NDI_TAP_FD as
 *   one record, all little-endian:
 *
 *       len u32 | flags u8 | pts i64 | arrived_ns i64 | payload
 *
 *   flags bit 0 is AV_PKT_FLAG_KEY. pts is the packet's own: the camera's
 *   clock (AV_NOPTS_VALUE if it has none). arrived_ns is CLOCK_MONOTONIC as
 *   libndi makes the call, taken here because the reader may get to the
 *   record much later (below). The payload is the Annex-B bytes the camera
 *   sent. Every packet is passed on as it comes, P-slices before the first
 *   SPS/IDR too; the reader (tap.py) decides what to keep.
 * - Unless NDI_TAP_DECODE is "1", libndi's own decode of that context is off:
 *   send_packet takes the packet without decoding it and avcodec_receive_frame
 *   answers EAGAIN. libndi still passes all 60 packets/s, and a camera costs
 *   ~0.02 of a core instead of ~0.5 (measured 2026-10-24).
 *
 * libndi's thread never waits on the reader. It queues each record for a
 * writer thread of the tap's own, which writes whole records to the fd and
 * may block there. A capture's Python reader can be held off the GIL while
 * cyndilib's disconnect/connect_to wait on libndi's thread; when that thread
 * itself wrote to the pipe, a full pipe froze the capture for good
 * (2026-10-25). A record that would take the queue past TAP_QUEUE_BYTES is
 * dropped, and so is every packet after it up to the next keyframe, so what
 * the reader gets still decodes.
 *
 * Built in Dockerfile.worker against the image's own libavcodec-dev, so
 * AVPacket and AVCodec are the layouts of the libavcodec libndi then loads.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <limits.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include <libavcodec/avcodec.h>

/* About 3 s of the busiest camera (~12 Mbit/s): the reader normally keeps the
 * queue near empty, and a hold-up longer than this is not worth catching up. */
#ifndef TAP_QUEUE_BYTES
#define TAP_QUEUE_BYTES (4 << 20)
#endif

static AVCodecContext *(*real_alloc_context3)(const AVCodec *);
static int (*real_send_packet)(AVCodecContext *, const AVPacket *);
static int (*real_receive_frame)(AVCodecContext *, AVFrame *);

static pthread_once_t once = PTHREAD_ONCE_INIT;
static AVCodecContext *h264;
static int tap_fd = -1;
static int decode;

/* A record's header: len u32, flags u8, pts i64, arrived_ns i64. */
#define HEADER 21

/* Records waiting for the writer, oldest first, and what decides whether the
 * next one may join them. All under `lock`. */
struct record {
    struct record *next;
    size_t size;
    uint8_t bytes[];
};
static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t queued = PTHREAD_COND_INITIALIZER;
static struct record *first, *last;
static size_t queued_bytes;
static int need_key;        /* a packet was dropped: queue nothing before a keyframe */
static long dropped;        /* packets dropped since the last one queued */

static void *real(void *lib, const char *name) {
    void *f = lib ? dlsym(lib, name) : NULL;
    if (!f) {
        fprintf(stderr, "ndi_tap: no %s in libavcodec.so.61: %s\n", name, dlerror());
        abort();
    }
    return f;
}

static int write_all(int fd, const void *buf, size_t n) {
    const uint8_t *p = buf;
    while (n > 0) {
        ssize_t w = write(fd, p, n);
        if (w < 0) {
            if (errno == EINTR)
                continue;
            return -1;
        }
        p += w;
        n -= (size_t)w;
    }
    return 0;
}

static void put_le(uint8_t *p, uint64_t v, int n) {
    for (int i = 0; i < n; i++, v >>= 8)
        p[i] = v & 0xff;
}

static void *writer(void *arg) {
    int fd = (int)(intptr_t)arg;
    for (;;) {
        pthread_mutex_lock(&lock);
        while (!first)
            pthread_cond_wait(&queued, &lock);
        struct record *r = first;
        first = r->next;
        if (!first)
            last = NULL;
        queued_bytes -= r->size;
        pthread_mutex_unlock(&lock);

        int failed = write_all(fd, r->bytes, r->size) < 0;
        free(r);
        if (failed) {
            /* The reader is gone. Stop tapping; libndi carries on. */
            fprintf(stderr, "ndi_tap: write to NDI_TAP_FD failed: %s; not tapping\n",
                    strerror(errno));
            __atomic_store_n(&tap_fd, -1, __ATOMIC_RELAXED);
            pthread_mutex_lock(&lock);
            while (first) {
                r = first;
                first = r->next;
                free(r);
            }
            last = NULL;
            queued_bytes = 0;
            pthread_mutex_unlock(&lock);
            return NULL;
        }
    }
}

static void init(void) {
    /* Already loaded as our NEEDED library; this only finds its handle, whose
     * own definitions come before ours. */
    void *lib = dlopen("libavcodec.so.61", RTLD_NOW | RTLD_NOLOAD);
    real_alloc_context3 = real(lib, "avcodec_alloc_context3");
    real_send_packet = real(lib, "avcodec_send_packet");
    real_receive_frame = real(lib, "avcodec_receive_frame");

    const char *fd = getenv("NDI_TAP_FD");
    if (fd && *fd) {
        char *end;
        long v = strtol(fd, &end, 10);
        if (*end == '\0' && v >= 0 && v <= INT_MAX)
            tap_fd = (int)v;
        else
            fprintf(stderr, "ndi_tap: NDI_TAP_FD=%s is not a file descriptor; not tapping\n", fd);
    }
    pthread_t t;
    if (tap_fd >= 0 && pthread_create(&t, NULL, writer, (void *)(intptr_t)tap_fd) == 0)
        pthread_detach(t);
    else if (tap_fd >= 0) {
        fprintf(stderr, "ndi_tap: no writer thread; not tapping\n");
        tap_fd = -1;
    }
    const char *d = getenv("NDI_TAP_DECODE");
    decode = d && strcmp(d, "1") == 0;
}

static int is_h264(const AVCodecContext *ctx) {
    return ctx && ctx == __atomic_load_n(&h264, __ATOMIC_ACQUIRE);
}

static void tap(const AVPacket *pkt) {
    if (__atomic_load_n(&tap_fd, __ATOMIC_RELAXED) < 0 || !pkt || !pkt->data || pkt->size <= 0)
        return;
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    int64_t arrived = (int64_t)now.tv_sec * 1000000000 + now.tv_nsec;
    uint32_t n = (uint32_t)pkt->size;
    int key = (pkt->flags & AV_PKT_FLAG_KEY) != 0;
    size_t size = HEADER + (size_t)n;

    pthread_mutex_lock(&lock);
    if ((need_key && !key) || queued_bytes + size > TAP_QUEUE_BYTES) {
        if (!need_key)
            fprintf(stderr, "ndi_tap: the reader is %zu bytes behind; dropping to the next "
                    "keyframe\n", queued_bytes);
        need_key = 1;
        dropped++;
        pthread_mutex_unlock(&lock);
        return;
    }
    pthread_mutex_unlock(&lock);

    struct record *r = malloc(sizeof *r + size);
    if (!r)
        return;
    r->next = NULL;
    r->size = size;
    uint8_t *p = r->bytes;
    put_le(p, n, 4);
    p[4] = key;
    put_le(p + 5, (uint64_t)pkt->pts, 8);
    put_le(p + 13, (uint64_t)arrived, 8);
    memcpy(p + HEADER, pkt->data, n);

    pthread_mutex_lock(&lock);
    if (need_key)
        fprintf(stderr, "ndi_tap: queueing again at a keyframe, %ld packets dropped\n", dropped);
    need_key = 0;
    dropped = 0;
    if (last)
        last->next = r;
    else
        first = r;
    last = r;
    queued_bytes += size;
    pthread_cond_signal(&queued);
    pthread_mutex_unlock(&lock);
}

AVCodecContext *avcodec_alloc_context3(const AVCodec *codec) {
    pthread_once(&once, init);
    AVCodecContext *ctx = real_alloc_context3(codec);
    if (ctx && codec && codec->id == AV_CODEC_ID_H264)
        __atomic_store_n(&h264, ctx, __ATOMIC_RELEASE);
    else if (is_h264(ctx))
        /* A freed video context's memory, reused for another codec. */
        __atomic_store_n(&h264, NULL, __ATOMIC_RELEASE);
    return ctx;
}

int avcodec_send_packet(AVCodecContext *ctx, const AVPacket *pkt) {
    pthread_once(&once, init);
    if (!is_h264(ctx))
        return real_send_packet(ctx, pkt);
    tap(pkt);
    return decode ? real_send_packet(ctx, pkt) : 0;
}

int avcodec_receive_frame(AVCodecContext *ctx, AVFrame *frame) {
    pthread_once(&once, init);
    if (is_h264(ctx) && !decode)
        return AVERROR(EAGAIN);
    return real_receive_frame(ctx, frame);
}
