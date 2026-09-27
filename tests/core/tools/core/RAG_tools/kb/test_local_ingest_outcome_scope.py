"""An ingest may only inherit rollback status from its own collection operation."""

from xagent.core.tools.core.RAG_tools.core.schemas import IngestionResult
from xagent.core.tools.core.RAG_tools.kb import (
    KBApiCompatibilityFacade,
    KBApiOperationResult,
    KBCoordinator,
    KBOperationCompatibilityFacade,
)


def test_failed_ingest_does_not_inherit_other_collection_operation() -> None:
    operations = KBOperationCompatibilityFacade()
    facade = KBCoordinator(operation_compatibility=operations).api_compatibility
    with operations.start_operation(
        operation_type="document_ingestion", collection="private"
    ) as active:
        active.finish(status="error", side_effects_may_remain=True)

    scoped = facade.wrap_operation_result(
        IngestionResult(status="error", message="local ingest failed"),
        operation_type="document_ingestion",
        collection="shared",
    )
    assert scoped.operation_outcome is None
    assert (
        facade.failed_ingest_cleanup_decision(scoped).side_effects_may_remain is False
    )


def test_malformed_document_count_cannot_publish_a_successful_batch() -> None:
    facade = KBApiCompatibilityFacade()
    failed = KBApiOperationResult(
        result={"status": "partial", "documents_created": "not-a-count"}
    )
    decision = facade.failed_batch_ingest_cleanup_decision([failed])
    assert decision.successful_documents == 0
