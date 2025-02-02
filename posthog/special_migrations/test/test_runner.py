from datetime import datetime

import pytest
from django.db import connection

from posthog.models.special_migration import MigrationStatus, SpecialMigration
from posthog.models.utils import UUIDT
from posthog.special_migrations.examples.test import Migration
from posthog.special_migrations.runner import (
    attempt_migration_rollback,
    run_special_migration_next_op,
    start_special_migration,
)
from posthog.special_migrations.test.util import create_special_migration
from posthog.special_migrations.utils import update_special_migration
from posthog.test.base import BaseTest

TEST_MIGRATION_DESCRIPTION = Migration().description


class TestRunner(BaseTest):
    def setUp(self):
        create_special_migration(name="test", description=TEST_MIGRATION_DESCRIPTION)
        return super().setUp()

    # Run the full migration through
    @pytest.mark.ee
    def test_run_migration_in_full(self):
        migration_successful = start_special_migration("test")
        sm = SpecialMigration.objects.get(name="test")

        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM test_special_migration")
            res = cursor.fetchone()

        self.assertEqual(res, ("a", "c"))

        self.assertTrue(migration_successful)
        self.assertEqual(sm.name, "test")
        self.assertEqual(sm.description, TEST_MIGRATION_DESCRIPTION)
        self.assertEqual(sm.status, MigrationStatus.CompletedSuccessfully)
        self.assertEqual(sm.progress, 100)
        self.assertEqual(sm.last_error, "")
        self.assertTrue(UUIDT.is_valid_uuid(sm.current_query_id))
        self.assertEqual(sm.current_operation_index, 4)
        self.assertEqual(sm.posthog_min_version, "1.0.0")
        self.assertEqual(sm.posthog_max_version, "100000.0.0")
        self.assertEqual(sm.finished_at.day, datetime.today().day)

    @pytest.mark.ee
    def test_rollback_migration(self):

        migration_successful = start_special_migration("test")

        self.assertEqual(migration_successful, True)

        sm = SpecialMigration.objects.get(name="test")

        attempt_migration_rollback(sm)
        sm.refresh_from_db()

        exception = None
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT * FROM test_special_migration")
        except Exception as e:
            exception = e

        self.assertTrue('relation "test_special_migration" does not exist' in str(exception))

        self.assertEqual(sm.status, MigrationStatus.RolledBack)
        self.assertEqual(sm.progress, 0)

    @pytest.mark.ee
    def test_run_special_migration_next_op(self):
        sm = SpecialMigration.objects.get(name="test")

        update_special_migration(sm, status=MigrationStatus.Running)

        run_special_migration_next_op("test", sm, run_all=False)

        sm.refresh_from_db()
        self.assertEqual(sm.current_operation_index, 1)
        self.assertEqual(sm.progress, int(100 * 1 / 4))

        run_special_migration_next_op("test", sm, run_all=False)

        sm.refresh_from_db()
        self.assertEqual(sm.current_operation_index, 2)
        self.assertEqual(sm.progress, int(100 * 2 / 4))

        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM test_special_migration")
            res = cursor.fetchone()

        self.assertEqual(res, ("a", "b"))

        run_special_migration_next_op("test", sm, run_all=False)
        run_special_migration_next_op("test", sm, run_all=False)
        run_special_migration_next_op("test", sm, run_all=False)

        sm.refresh_from_db()
        self.assertEqual(sm.current_operation_index, 4)
        self.assertEqual(sm.progress, 100)
        self.assertEqual(sm.status, MigrationStatus.CompletedSuccessfully)

        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM test_special_migration")
            res = cursor.fetchone()

        self.assertEqual(res, ("a", "c"))

    @pytest.mark.ee
    def test_rollback_an_incomplete_migration(self):
        sm = SpecialMigration.objects.get(name="test")
        sm.status = MigrationStatus.Running
        sm.save()

        run_special_migration_next_op("test", sm, run_all=False)
        run_special_migration_next_op("test", sm, run_all=False)

        sm.refresh_from_db()
        self.assertEqual(sm.current_operation_index, 2)

        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM test_special_migration")
            res = cursor.fetchone()

        self.assertEqual(res, ("a", "b"))

        attempt_migration_rollback(sm)
        sm.refresh_from_db()

        exception = None
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT * FROM test_special_migration")
        except Exception as e:
            exception = e

        self.assertTrue('relation "test_special_migration" does not exist' in str(exception))
        self.assertEqual(sm.status, MigrationStatus.RolledBack)
        self.assertEqual(sm.progress, 0)
