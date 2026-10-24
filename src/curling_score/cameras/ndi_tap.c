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
 * - avcodec_send_packet on that context writes the packet to NDI_TAP_FD as one
 *   record: the payload length (4 bytes, little-endian), a flags byte (bit 0 =
 *   AV_PKT_FLAG_KEY) and the payload, the Annex-B bytes the camera sent. Every
 *   packet is written as it comes, P-slices before the first SPS/IDR too; the
 *   reader (tap.py) decides what to keep. Give it a blocking fd: a reader that
 *   falls behind then holds up libndi's decode thread rather than losing parts
 *   of records.
 * - Unless NDI_TAP_DECODE is "1", libndi's own decode of that context is off:
 *   send_packet takes the packet without decoding it and avcodec_receive_frame
 *   answers EAGAIN. libndi still passes all 60 packets/s, and a camera costs
 *   ~0.02 of a core instead of ~0.5 (measured 2026-10-24).
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
#include <unistd.h>

#include <libavcodec/avcodec.h>

static AVCodecContext *(*real_alloc_context3)(const AVCodec *);
static int (*real_send_packet)(AVCodecContext *, const AVPacket *);
static int (*real_receive_frame)(AVCodecContext *, AVFrame *);

static pthread_once_t once = PTHREAD_ONCE_INIT;
static AVCodecContext *h264;
static int tap_fd = -1;
static int decode;

static void *real(void *lib, const char *name) {
    void *f = lib ? dlsym(lib, name) : NULL;
    if (!f) {
        fprintf(stderr, "ndi_tap: no %s in libavcodec.so.61: %s\n", name, dlerror());
        abort();
    }
    return f;
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
    const char *d = getenv("NDI_TAP_DECODE");
    decode = d && strcmp(d, "1") == 0;
}

static int is_h264(const AVCodecContext *ctx) {
    return ctx && ctx == __atomic_load_n(&h264, __ATOMIC_ACQUIRE);
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

static void tap(const AVPacket *pkt) {
    int fd = __atomic_load_n(&tap_fd, __ATOMIC_RELAXED);
    if (fd < 0 || !pkt || !pkt->data || pkt->size <= 0)
        return;
    uint32_t n = (uint32_t)pkt->size;
    uint8_t head[5] = {n & 0xff, (n >> 8) & 0xff, (n >> 16) & 0xff, n >> 24,
                       (pkt->flags & AV_PKT_FLAG_KEY) ? 1 : 0};
    if (write_all(fd, head, sizeof head) < 0 || write_all(fd, pkt->data, n) < 0) {
        /* The reader is gone. Stop writing; libndi carries on. */
        fprintf(stderr, "ndi_tap: write to NDI_TAP_FD failed: %s; not tapping\n", strerror(errno));
        __atomic_store_n(&tap_fd, -1, __ATOMIC_RELAXED);
    }
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
