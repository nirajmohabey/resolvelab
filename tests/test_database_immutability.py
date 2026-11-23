"""Published evidence remains immutable even through the migration owner."""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from resolvelab.db import admin_engine


@pytest.mark.parametrize("statement",[
    "UPDATE resolvelab.model_versions SET artifact_sha256=repeat('0',64)",
    "UPDATE resolvelab.releases SET prompt_version='changed'",
    "UPDATE resolvelab.chunks SET body='changed policy'",
    "UPDATE resolvelab.documents SET access_scope='admin'",
    "UPDATE resolvelab.corpus_versions SET content_sha256=repeat('0',64)",
    "DELETE FROM resolvelab.chunks",
])
def test_published_records_reject_administrative_overwrite(statement):
    with pytest.raises(IntegrityError):
        with admin_engine.begin() as conn:conn.execute(text(statement))
