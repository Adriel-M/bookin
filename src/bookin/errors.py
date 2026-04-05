class BookinError(Exception):
    pass


class CalibreNotFoundError(BookinError):
    pass


class CalibreCommandError(BookinError):
    pass


class MetadataFetchError(BookinError):
    pass


class ProcessingError(BookinError):
    pass
