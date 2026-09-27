from xagent.core.model.embedding import (
    BaseEmbedding,
    OpenAIEmbedding,
    XinferenceEmbedding,
)


class TestAbstractEmbeddingInterface:
    def test_openai_compatible_implements_base_embedding(self):
        embedding = OpenAIEmbedding(
            model="lan-embedding", base_url="http://model.internal/v1"
        )
        assert isinstance(embedding, BaseEmbedding)
        assert hasattr(embedding, "encode")
        assert hasattr(embedding, "get_dimension")
        assert hasattr(embedding, "abilities")

    def test_xinference_implements_base_embedding(self):
        embedding = XinferenceEmbedding(
            model="local-embedding", base_url="http://model.internal"
        )
        assert isinstance(embedding, BaseEmbedding)
