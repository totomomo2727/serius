"""How much weight a piece earns for where it came from.

The library is curated, but not every source is equally worth a reader's three
slots. Sources fall into three tiers:

1. The thinker themselves, or the primary text: a writer's own site, a peer
   reviewed paper, a scholarly reference work, an original talk or a magazine
   whose whole business is long-form ideas (Aeon, Psyche).
2. Strong specialist publications: research groups, trade journals and careful
   secondary writing.
3. Everything else: general outlets, aggregators, summaries of other people's
   thinking.

Platform domains (YouTube, podcast hosts) say nothing about the source, so items
published there carry an explicit ``source_tier`` in the library file, chosen for
the channel or show rather than the platform.
"""

from __future__ import annotations

from urllib.parse import urlparse

DEFAULT_TIER = 3

# The tier weights are deliberately smaller than an interest match: a stronger
# source decides between comparable pieces, it does not override relevance.
TIER_WEIGHTS = {1: 5.0, 2: 2.5, 3: 0.0}

PLATFORM_DOMAINS = {
    "youtube.com",
    "youtu.be",
    "podcasts.apple.com",
    "open.spotify.com",
    "soundcloud.com",
    "vimeo.com",
}

TIER_ONE = {
    # writers and makers publishing their own work
    "paulgraham.com",
    "gwern.net",
    "worrydream.com",
    "karpathy.github.io",
    "craigmod.com",
    "calnewport.com",
    "stratechery.com",
    "simonwillison.net",
    "thesephist.com",
    "maggieappleton.com",
    "geoffreylitt.com",
    "wattenberger.com",
    "jalammar.github.io",
    "frankchimero.com",
    "idlewords.com",
    "joelonsoftware.com",
    "practicaltypography.com",
    "augmentingcognition.com",
    "writings.stephenwolfram.com",
    "ncase.me",
    "mcfunley.com",
    "nabeelqu.substack.com",
    "anildash.com",
    "pluralistic.net",
    "ia.net",
    "resilientwebdesign.com",
    "atomicdesign.bradfrost.com",
    "oneusefulthing.org",
    "anthropic.com",
    # primary texts, scholarship and reference works
    "plato.stanford.edu",
    "iep.utm.edu",
    "arxiv.org",
    "distill.pub",
    "dl.acm.org",
    "gutenberg.org",
    "wikisource.org",
    "selfdeterminationtheory.org",
    "its.caltech.edu",
    "rintintin.colorado.edu",
    "cs.utexas.edu",
    "w3.org",
    # magazines built entirely around long-form ideas
    "aeon.co",
    "psyche.co",
}

TIER_TWO = {
    "nngroup.com",
    "alistapart.com",
    "fs.blog",
    "blog.codinghorror.com",
    "learningscientists.org",
    "jamesclear.com",
    "waitbutwhy.com",
    "effectivealtruism.org",
    "rauno.me",
    "newyorker.com",
    "theatlantic.com",
}


def domain(url: str) -> str:
    host = urlparse(url).netloc.lower()
    host = host.split(":")[0].removeprefix("www.")
    return host


def is_platform(url: str) -> bool:
    host = domain(url)
    return host in PLATFORM_DOMAINS or host.endswith(".libsyn.com")


def tier_for(url: str, declared: int | None = None) -> int:
    """The library may state a tier (needed for platform-hosted pieces); else infer."""
    if declared in TIER_WEIGHTS:
        return int(declared)
    host = domain(url)
    parts = host.split(".")
    for candidate in (host, ".".join(parts[-2:]) if len(parts) > 2 else host):
        if candidate in TIER_ONE:
            return 1
        if candidate in TIER_TWO:
            return 2
    return DEFAULT_TIER


def source_score(tier: int | None) -> float:
    return TIER_WEIGHTS.get(tier or DEFAULT_TIER, 0.0)
