from django.db import models


# an enum, essentially
class MigrationStatus:
    NotStarted = 0
    Running = 1
    CompletedSuccessfully = 2
    Errored = 3
    RolledBack = 4
    Starting = 5  # only relevant for the UI


class SpecialMigration(models.Model):
    class Meta:
        constraints = [models.UniqueConstraint(fields=["name"], name="unique name",)]

    id: models.BigAutoField = models.BigAutoField(primary_key=True)
    name: models.CharField = models.CharField(max_length=50, null=False, blank=False)
    description: models.CharField = models.CharField(max_length=400, null=True, blank=True)
    progress: models.PositiveSmallIntegerField = models.PositiveSmallIntegerField(null=False, blank=False, default=0)
    status: models.PositiveSmallIntegerField = models.PositiveSmallIntegerField(
        null=False, blank=False, default=MigrationStatus.NotStarted
    )

    current_operation_index: models.PositiveSmallIntegerField = models.PositiveSmallIntegerField(
        null=False, blank=False, default=0
    )
    current_query_id: models.CharField = models.CharField(max_length=100, null=False, blank=False, default="")
    celery_task_id: models.CharField = models.CharField(max_length=100, null=False, blank=False, default="")

    started_at: models.DateTimeField = models.DateTimeField(null=True, blank=True)

    # Can finish with status 'CompletedSuccessfully', 'Errored', or 'RolledBack'
    finished_at: models.DateTimeField = models.DateTimeField(null=True, blank=True)

    last_error: models.TextField = models.TextField(null=True, blank=True)
    posthog_min_version: models.CharField = models.CharField(max_length=20, null=True, blank=True)
    posthog_max_version: models.CharField = models.CharField(max_length=20, null=True, blank=True)


def get_all_completed_special_migrations():
    return SpecialMigration.objects.filter(status=MigrationStatus.CompletedSuccessfully)


def get_all_running_special_migrations():
    return SpecialMigration.objects.filter(status=MigrationStatus.Running)


# allow for splitting code paths
def is_special_migration_complete(migration_name: str) -> bool:
    migration_instance = SpecialMigration.objects.filter(
        name=migration_name, status=MigrationStatus.CompletedSuccessfully
    ).first()
    return migration_instance is not None
