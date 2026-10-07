from .arxiv_fetcher import ArxivFetcher
from .upload_fetcher import UploadFetcher

FETCHERS = {"arxiv": ArxivFetcher(), "upload": UploadFetcher()}