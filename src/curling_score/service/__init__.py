"""The hosted service: submit a link, get a charting URL, chart together.

The pipeline itself is unchanged. This package adds the parts a public site
needs around it -- identity for sources and links, a queue a worker can pull
from, storage that outlives any one machine, and the small amount of HTTP that
ties them together. Every external system sits behind a protocol with an
in-memory twin, so the whole service runs in a test without a cloud account.
"""
