import meilisearch

from app.core.config import settings

client = meilisearch.Client(settings.meilisearch_url, settings.meilisearch_api_key, timeout=5)

INDEX_NAME = "segments"


def get_search_index():
    return client.index(INDEX_NAME)


def setup_search_index():
    """Create and configure the Meilisearch index."""
    index = client.create_index(INDEX_NAME, {"primaryKey": "id"})
    client.index(INDEX_NAME).update_filterable_attributes(
        ["episode_id", "show", "speaker"]
    )
    client.index(INDEX_NAME).update_searchable_attributes(["text"])
    client.index(INDEX_NAME).update_sortable_attributes(["start_time"])
    # Better Chinese tokenization
    client.index(INDEX_NAME).update_dictionary([])
    client.index(INDEX_NAME).update_pagination_settings({"maxTotalHits": 5000})
    return index
