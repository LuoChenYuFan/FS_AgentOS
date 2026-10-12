"""Retrieval failures. One dead backend is not a failure; both dead is."""


class RetrievalError(Exception):
    """Lexical and vector search both failed."""


class RetrievalBackendError(Exception):
    """One search backend could not answer."""
