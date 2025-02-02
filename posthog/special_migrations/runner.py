from typing import List, Optional, Tuple

from django.db.transaction import rollback
from semantic_version.base import SimpleSpec

from posthog.models.special_migration import MigrationStatus, SpecialMigration, get_all_running_special_migrations
from posthog.models.utils import UUIDT
from posthog.settings import SPECIAL_MIGRATIONS_ROLLBACK_TIMEOUT
from posthog.special_migrations.setup import (
    POSTHOG_VERSION,
    get_special_migration_definition,
    get_special_migration_dependency,
)
from posthog.special_migrations.utils import (
    complete_migration,
    execute_op,
    mark_special_migration_as_running,
    process_error,
    trigger_migration,
    update_special_migration,
)
from posthog.version_requirement import ServiceVersionRequirement

"""
Important to prevent us taking up too many celery workers and also to enable running migrations sequentially
"""
MAX_CONCURRENT_SPECIAL_MIGRATIONS = 1


def start_special_migration(migration_name: str, ignore_posthog_version=False) -> bool:
    """
    Performs some basic checks to ensure the migration can indeed run, and then kickstarts the chain of operations
    Checks:
    1. We're not over the concurrent migrations limit
    2. The migration can be run with the current PostHog version
    3. The migration is not already running
    4. The migration is required given the instance configuration
    5. The service version requirements are met (e.g. X < ClickHouse version < Y)
    6. The migration's healthcheck passes
    7. The migration's dependency has been completed
    """

    migration_instance = SpecialMigration.objects.get(name=migration_name)
    over_concurrent_migrations_limit = len(get_all_running_special_migrations()) >= MAX_CONCURRENT_SPECIAL_MIGRATIONS
    posthog_version_valid = ignore_posthog_version or is_posthog_version_compatible(
        migration_instance.posthog_min_version, migration_instance.posthog_max_version
    )

    if (
        not migration_instance
        or over_concurrent_migrations_limit
        or not posthog_version_valid
        or migration_instance.status == MigrationStatus.Running
    ):
        return False

    migration_definition = get_special_migration_definition(migration_name)

    if not migration_definition.is_required():
        complete_migration(migration_instance)
        return True

    ok, error = check_service_version_requirements(migration_definition.service_version_requirements)
    if not ok:
        process_error(migration_instance, error)
        return False

    ok, error = run_migration_healthcheck(migration_instance)
    if not ok:
        process_error(migration_instance, error)
        return False

    ok, error = is_migration_dependency_fulfilled(migration_instance.name)
    if not ok:
        process_error(migration_instance, error)
        return False

    mark_special_migration_as_running(migration_instance)

    return run_special_migration_next_op(migration_name, migration_instance)


def run_special_migration_next_op(
    migration_name: str, migration_instance: Optional[SpecialMigration] = None, run_all=True
):
    """
    Runs the next operation specified by the currently running migration
    If `run_all=True`, we run through all operations recursively, else we run one and return
    Terminology:
    - migration_instance: The migration object as stored in the DB 
    - migration_definition: The actual migration class outlining the operations (e.g. special_migrations/examples/example.py)
    """

    if not migration_instance:
        try:
            migration_instance = SpecialMigration.objects.get(name=migration_name, status=MigrationStatus.Running)
        except SpecialMigration.DoesNotExist:
            return False
    else:
        migration_instance.refresh_from_db()

    assert migration_instance is not None

    migration_definition = get_special_migration_definition(migration_name)
    if migration_instance.current_operation_index > len(migration_definition.operations) - 1:
        complete_migration(migration_instance)
        return True

    op = migration_definition.operations[migration_instance.current_operation_index]

    error = None
    current_query_id = str(UUIDT())

    try:
        execute_op(op, current_query_id)
        update_special_migration(
            migration_instance=migration_instance,
            current_query_id=current_query_id,
            current_operation_index=migration_instance.current_operation_index + 1,
        )

    except Exception as e:
        error = str(e)
        process_error(migration_instance, error)

    if error:
        return False

    update_migration_progress(migration_instance)

    # recursively run through all operations
    if run_all:
        return run_special_migration_next_op(migration_name, migration_instance)


def run_migration_healthcheck(migration_instance: SpecialMigration):
    return get_special_migration_definition(migration_instance.name).healthcheck()


def update_migration_progress(migration_instance: SpecialMigration):
    """
    We don't want to interrupt a migration if the progress check fails, hence try without handling exceptions
    Progress is a nice-to-have bit of feedback about how the migration is doing, but not essential
    """

    migration_instance.refresh_from_db()
    try:
        progress = get_special_migration_definition(migration_instance.name).progress(
            migration_instance  # type: ignore
        )
        update_special_migration(migration_instance=migration_instance, progress=progress)
    except:
        pass


def attempt_migration_rollback(migration_instance: SpecialMigration, force: bool = False):
    """
    Cycle through the operations in reverse order starting from the last completed op and run
    the specified rollback statements.
    """
    migration_instance.refresh_from_db()

    try:
        ops = get_special_migration_definition(migration_instance.name).operations

        i = len(ops) - 1
        for op in reversed(ops[: migration_instance.current_operation_index + 1]):
            if not op.rollback:
                if op.rollback == "":
                    continue
                raise Exception(f"No rollback provided for operation at index {i}: {op.sql}")
            execute_op(op, str(UUIDT()), rollback=True)
            i -= 1
    except Exception as e:
        error = str(e)

        # forced rollbacks are when the migration completed successfully but the user
        # still requested a rollback, in which case we set the error to whatever happened
        # while rolling back. under normal circumstances, the error is reserved to
        # things that happened during the migration itself
        if force:
            update_special_migration(
                migration_instance=migration_instance,
                status=MigrationStatus.Errored,
                last_error=f"Force rollback failed with error: {error}",
            )

        return

    update_special_migration(migration_instance=migration_instance, status=MigrationStatus.RolledBack, progress=0)


def is_current_operation_resumable(migration_instance: SpecialMigration):
    migration_definition = get_special_migration_definition(migration_instance.name)
    index = migration_instance.current_operation_index
    return migration_definition.operations[index].resumable


def is_posthog_version_compatible(posthog_min_version, posthog_max_version):
    return POSTHOG_VERSION in SimpleSpec(f">={posthog_min_version},<={posthog_max_version}")


def run_next_migration(candidate: str, after_delay: int = 0):
    migration_instance = SpecialMigration.objects.get(name=candidate)
    migration_in_range = is_posthog_version_compatible(
        migration_instance.posthog_min_version, migration_instance.posthog_max_version
    )

    dependency_ok, _ = is_migration_dependency_fulfilled(candidate)

    if dependency_ok and migration_in_range and migration_instance.status == MigrationStatus.NotStarted:
        trigger_migration(migration_instance, countdown=after_delay)


def is_migration_dependency_fulfilled(migration_name: str) -> Tuple[bool, Optional[str]]:
    dependency = get_special_migration_dependency(migration_name)

    dependency_ok: bool = (
        not dependency or SpecialMigration.objects.get(name=dependency).status == MigrationStatus.CompletedSuccessfully
    )
    error = f"Could not trigger migration because it depends on {dependency}" if not dependency_ok else None
    return dependency_ok, error


def check_service_version_requirements(
    service_version_requirements: List[ServiceVersionRequirement],
) -> Tuple[bool, Optional[str]]:
    for service_version_requirement in service_version_requirements:
        in_range, version = service_version_requirement.is_service_in_accepted_version()
        if not in_range:
            return (
                False,
                f"Service {service_version_requirement.service} is in version {version}. Expected range: {str(service_version_requirement.supported_version)}.",
            )

    return True, None
