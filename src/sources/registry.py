from .google_news import GoogleNewsSource

SOURCE_MAP = {
    "google_news": GoogleNewsSource,
}


def get_sources(config):
    sources = []
    for name in config.sources:
        cls = SOURCE_MAP.get(name)
        if cls:
            sources.append(cls(config))
    return sources
