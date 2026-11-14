"""Data sources. Each fetch function returns data or raises SourceError."""


class SourceError(Exception):
    """A data source could not be read. The pipeline turns this into a
    source_status entry instead of failing the request."""
