Title: Wagtail: indexing hundreds of thousands of payments with PostgreSQL
Date: 2026-09-21 15:00
Id: 0007
Slug: wagtail-indexing-payments-with-postgresql
Lang: en
Category: development
Tags: django, wagtail, postgresql
Summary: How RGIVE kept slow searches on a PostgreSQL instance shared by all its associations from becoming a bottleneck, by combining Wagtail indexing with 20-minute reindexing batches.

# The need

RGIVE hosts the payments and donation commitments of all its client associations on a single PostgreSQL instance. Each association is isolated in its own PostgreSQL schema, for security reasons: no lateral movement is possible from one client to another. This database grows every day with new donations; the payment tables of our largest associations now exceed several hundred thousand rows.

Our CSMs regularly need to find a specific donation from the donor's email, or from their first and last name. The catch is that email, first name and last name are not columns of the payments table: they live on the `Contact` model, linked to the payment through a `ForeignKey`. Finding "all payments for this email" is therefore a cross-table search.

We first went for the simplest option, with the tools already at hand: Django and its native search — a `JOIN` to `Contact`, filtered with an `icontains` on the email or the name. It held up for a while, then the tables grew. On top of the join, every search ended up triggering a full sequential scan (`Seq Scan`) on tables of several hundred thousand rows — queries that started taking several seconds.

On a database dedicated to a single client, a slow query would stay an isolated problem. In our case, even though each association is isolated in its own schema, they all run on the same PostgreSQL instance. A query that takes several seconds does not only penalize the person running it: it consumes CPU, I/O and connections that everyone else is using at the same time. A poorly indexed search is not a mere inconvenience, it is a bottleneck that puts every client on that database at risk.

# Why Wagtail to index our payments?

RGIVE already uses Wagtail as its CMS and as the foundation of its admin. Wagtail ships with a search indexing system, [`wagtail.search`](https://docs.wagtail.org/en/stable/topics/search/index.html), originally designed to search CMS pages, but which works on any Django model.

`wagtail.search` can rely on several backends, Elasticsearch among them. We chose Wagtail's PostgreSQL search backend. Using it rather than a dedicated search engine (Elasticsearch, for example) saves us from adding a dependency: no new service to deploy, monitor and upgrade, and no synchronization to maintain between two databases. It is also a deliberate budget decision: Elasticsearch needs its own cluster, its own resources, and sysadmin time to run properly in production. By staying on PostgreSQL, we pay for capacity we already have, not for a second system to operate and monitor. As a bonus, it also spares our sysadmins a few 3 a.m. nightmares — they thank us too.

Wagtail's PostgreSQL backend relies on PostgreSQL's native full-text search features (`tsvector`, `GIN` index). No new infrastructure: the database that already runs the application also runs the search.

Configuration:

```python
WAGTAILSEARCH_BACKENDS = {
    "default": {
        "BACKEND": "wagtail.search.backends.database.postgres.postgres",
        "OPTIONS": {
            # "simple" for better partial matching
            # "french" would apply French stemming, but requires a full reindex
            "search_config": "simple",
        },
    }
}
```

We picked the `simple` configuration over `french`. PostgreSQL's `french` configuration applies stemming: for example, it brings "paiement" and "payer" (French for "payment" and "to pay") together. That is useful on free text, but not for finding a proper name or an email, where we want matching close to the raw text rather than a linguistic root.

We also kept an escape hatch to disable indexing entirely, which comes in handy in CI:

```python
if os.getenv("DISABLE_WAGTAILSEARCH_BACKENDS", "").strip().lower() in {"true", "yes"}:
    WAGTAILSEARCH_BACKENDS = {"default": {}}
```

# Indexing a field that lives on another table

In Wagtail, a model becomes indexable by inheriting from `index.Indexed` and declaring a `search_fields` list. Each field is declared with an index field type: `index.SearchField` for full-text search on free text, or `index.AutocompleteField` for prefix search.

At RGIVE, we use `index.AutocompleteField` on every indexed field, never `index.SearchField`. The reason is simple: we want to find "Durant" by typing only "Dur", which `AutocompleteField` supports out of the box, whereas a regular full-text search field looks for whole words.

That leaves the original problem: email, first name and last name are on `Contact`, not on `Payment`. Wagtail addresses exactly this case with `index.RelatedFields`, which indexes the fields of an object linked by a `ForeignKey` directly from the main model:

```python
from wagtail.search import index


class Conversion(index.Indexed, models.Model):
    """Abstract base class shared by Payment and Commitment."""

    search_fields = [
        index.AutocompleteField("uuid"),
        index.RelatedFields(
            "contact",
            [
                index.AutocompleteField("firstname"),
                index.AutocompleteField("lastname"),
                index.AutocompleteField("email"),
            ],
        ),
    ]
```

`Payment` and `Commitment` both inherit from this `Conversion` class, and therefore get the linked contact's indexing without redeclaring anything. Each one also adds its own `RelatedFields` to index the order identifiers stored on another related table (`order_id`, `transaction_id`...).

Result: a search by email or by name goes straight through Wagtail's Postgres index, with no join and no `Seq Scan` at query time — and without eating into the resources of the database everyone shares.

# Avoiding reindexing the same donation three times

Indexing our payments solved the read problem. A symmetrical problem remained on the write side: at RGIVE, a donation is not written in one go. Processing a payment saves it several times — at creation, then throughout the validation steps and when it gets attached to an order. In practice, the same payment is saved about three times during its processing.

By default, Wagtail reindexes an object on every save. Three saves per donation means three reindexations of the same object. During an activity peak of several thousand donations, for instance during a big campaign, that adds up to several thousand unnecessary reindexations within a few minutes — precisely when the database shared by all associations is already under the most load.

So we disabled Wagtail's automatic reindexing and built our own mechanism, based on two mixins:

- `IndexableMixin`, applied to `Payment` and `Commitment`. It turns off `search_auto_update`, adds a `needs_reindexing` field, and sets it to `True` on creation or as soon as an indexed field has actually changed (we compare a before/after snapshot around the save).
- `RelatedIndexableMixin`, applied to `Contact`. When an indexed field of `Contact` changes (a corrected email, for example), it finds the related payments and commitments and flags them as needing reindexing.

Every time an object is flagged, a single Celery task is scheduled 20 minutes later:

```python
def schedule_reindex_task_on_commit() -> None:
    """Schedule the global reindexing task 20 minutes in the future.
    If a task is already scheduled in the future, it is kept as is.
    """
    at = timezone.now() + timedelta(minutes=20)
    existing_task = (
        PeriodicTask.objects.select_related("clocked")
        .filter(name=IndexableMixin.REINDEX_TASK_REF)
        .first()
    )
    existing_schedule = (
        existing_task.clocked.clocked_time
        if existing_task and existing_task.clocked
        else None
    )
    if (
        existing_task
        and existing_task.enabled
        and existing_schedule is not None
        and existing_schedule >= timezone.now()
    ):
        return
    schedule_once(task_ref=IndexableMixin.REINDEX_TASK_REF, at=at, allow_redo=True)
```

The key part is the `if`: if a task is already scheduled in the future, we do nothing. Whether the donation triggers one, two or three saves in the following 20 minutes, only one reindexing task stays scheduled.

The task itself goes through each indexable model, fetches only the rows flagged `needs_reindexing=True`, processes them in batches with `itertools.batched`, sends them to the Postgres index, then clears the flag only on the processed rows:

```python
@celery_app.task
def reindex() -> None:
    backend = get_search_backend()
    indexable_models = [
        m for m in apps.get_models()
        if issubclass(m, IndexableMixin) and not m._meta.abstract
    ]
    for model in indexable_models:
        queryset = model.get_indexed_objects().filter(needs_reindexing=True).order_by("pk")
        index = backend.get_index_for_model(model)
        for batch in itertools.batched(queryset.iterator(), 100):
            index.add_items(model, batch)
            model.objects.filter(
                pk__in=[obj.pk for obj in batch], needs_reindexing=True
            ).update(needs_reindexing=False)
```

With this mechanism, a peak of several thousand donations never triggers several thousand immediate reindexations. It triggers a single reindexing pass, 20 minutes later, which processes in one go every object flagged in the meantime.

20 minutes may sound long. But this search is used in the admin, by our CSMs, and payment listings there are sorted from newest to oldest. A donation received in the last 20 minutes, not indexed yet, shows up on the first page anyway, without having to search for it. Search is mostly used to find older donations, buried in the following pages — and those have been indexed for a long time.

# Wrapping up

The starting point was a query performance problem on the database: slow searches on a PostgreSQL instance shared by all our associations (each isolated in its own schema, but all on the same instance). The answer comes down to two moves, both designed to avoid adding load to that database rather than shifting it elsewhere.

The first one is moving the cost to the right place: through `RelatedFields`, Wagtail can index fields stored on a related table, and PostgreSQL can then find them through an index rather than a `Seq Scan`. We needed neither an external search engine, nor the budget or the team to operate one.

The second one is not trading a read problem for a write problem: reindexing on every save would have multiplied the load on that same database at every donation peak. The 20-minute batching brings that cost down to a single pass, no matter how many saves happened in the meantime.

In the end, search became instant again, with no extra service and no extra line on the bill, and without making the other associations pay for the donation peaks of any one of them.
