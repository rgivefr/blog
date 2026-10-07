Title: Wagtail : indexer des centaines de milliers de paiements avec PostgreSQL
Date: 2026-09-21 15:00
Id: 0007
Slug: wagtail-indexer-paiements-avec-postgresql
Lang: fr
Category: développement
Tags: django, wagtail, postgresql
Summary: Comment RGIVE a évité qu'une recherche lente sur une base PostgreSQL partagée par toutes ses associations ne devienne un goulot d'étranglement, en combinant l'indexation Wagtail et un batching de réindexation à 20 minutes.

# Le besoin

RGIVE héberge, sur une même instance PostgreSQL, les paiements et les engagements de don de toutes ses associations clientes. Chaque association est isolée dans son propre schéma PostgreSQL, pour des raisons de sécurité : aucun mouvement latéral n'est possible d'un client à un autre. Cette base grossit chaque jour avec les nouveaux dons ; les tables de paiements de nos plus grosses associations dépassent aujourd'hui plusieurs centaines de milliers de lignes.

Nos CSM ont régulièrement besoin de retrouver un don précis à partir de l'email du donateur, ou de son nom et prénom. Le problème, c'est que l'email, le nom et le prénom ne sont pas des colonnes de la table des paiements : ils vivent sur le modèle `Contact`, relié au paiement par une `ForeignKey`. Retrouver « tous les paiements de cet email » est donc une recherche cross-table.

On est d'abord allé au plus simple, avec les outils déjà là : Django et sa recherche native — un `JOIN` vers `Contact`, filtré par un `icontains` sur l'email ou le nom. Ça a tenu un temps, puis les tables ont grossi. Chaque recherche a fini par déclencher, en plus de la jointure, un scan séquentiel complet (`Seq Scan`) sur des tables de plusieurs centaines de milliers de lignes — des requêtes qui se sont mises à prendre plusieurs secondes.

Sur une base dédiée à un seul client, une requête lente resterait un problème isolé. Chez nous, chaque association a beau être isolée dans son propre schéma, elles tournent toutes sur la même instance PostgreSQL. Une requête de plusieurs secondes ne pénalise pas que celle qui cherche : elle consomme du CPU, des I/O et des connexions que toutes les autres utilisent au même moment. Une recherche mal indexée n'est pas un simple inconfort, c'est un goulot d'étranglement qui met en risque l'ensemble des clients sur cette base.

# Pourquoi Wagtail pour indexer nos paiements ?

RGIVE utilise déjà Wagtail comme CMS et comme socle d'administration. Wagtail embarque un système d'indexation de recherche, [`wagtail.search`](https://docs.wagtail.org/en/stable/topics/search/index.html), conçu au départ pour chercher dans les pages du CMS, mais qui fonctionne sur n'importe quel modèle Django.

L'utiliser plutôt qu'un moteur de recherche dédié (Elasticsearch par exemple) nous évite d'ajouter une dépendance : pas de nouveau service à déployer, à surveiller, à faire évoluer, ni de synchronisation à maintenir entre deux bases. C'est aussi un choix budgétaire assumé : Elasticsearch demande son propre cluster, ses propres ressources, et du temps d'administration système pour le faire tourner correctement en production. En restant sur PostgreSQL, on paie pour de la capacité qu'on a de toute façon, pas pour un second système à opérer et à surveiller. Accessoirement, ça évite aussi quelques cauchemars de 3 h du matin à nos administrateurs systèmes — eux aussi nous remercient.

On a activé le backend PostgreSQL de Wagtail, qui s'appuie sur les fonctions de recherche plein texte natives de PostgreSQL (`tsvector`, index `GIN`). Pas de nouvelle infrastructure : la base de données qui fait déjà tourner l'application fait aussi tourner la recherche.

Configuration :

```python
WAGTAILSEARCH_BACKENDS = {
    "default": {
        "BACKEND": "wagtail.search.backends.database.postgres.postgres",
        "OPTIONS": {
            # "simple" pour un meilleur matching partiel
            # "french" ferait du stemming FR, mais demande une réindexation complète
            "search_config": "simple",
        },
    }
}
```

On a choisi la configuration `simple` plutôt que `french`. La configuration `french` de PostgreSQL fait du stemming : elle rapproche par exemple « paiement » et « payer ». C'est utile sur du texte libre, mais pas pour retrouver un nom propre ou un email, où on veut un matching proche du texte brut plutôt qu'une racine linguistique.

On a aussi gardé une échappatoire pour désactiver complètement l'indexation, utile en CI :

```python
if os.getenv("DISABLE_WAGTAILSEARCH_BACKENDS", "").strip().lower() in {"true", "yes"}:
    WAGTAILSEARCH_BACKENDS = {"default": {}}
```

# Indexer un champ qui vit sur une autre table

Dans Wagtail, un modèle devient indexable en héritant de `index.Indexed` et en déclarant une liste `search_fields`. Chaque champ est déclaré avec un type de champ d'index : `index.SearchField` pour du texte libre en plein texte, ou `index.AutocompleteField` pour de la recherche par préfixe.

Chez RGIVE, on utilise `index.AutocompleteField` sur tous les champs indexés, jamais `index.SearchField`. La raison est simple : on veut retrouver « Durant » en tapant seulement « Dur », ce qu'`AutocompleteField` permet directement, alors qu'un champ de recherche plein texte classique cherche des mots entiers.

Reste le problème de départ : l'email, le nom et le prénom sont sur `Contact`, pas sur `Payment`. Wagtail répond précisément à ce cas avec `index.RelatedFields`, qui indexe les champs d'un objet lié par `ForeignKey` directement depuis le modèle principal :

```python
from wagtail.search import index


class Conversion(index.Indexed, models.Model):
    """Classe abstraite commune à Payment et Commitment."""

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

`Payment` et `Commitment` héritent tous les deux de cette classe `Conversion`, et récupèrent donc l'indexation du contact lié sans rien redéclarer. Chacun ajoute en plus son propre `RelatedFields` pour indexer les identifiants de commande portés par une autre table liée (`order_id`, `transaction_id`...).

Résultat : une recherche par email ou par nom passe directement par l'index Postgres de Wagtail, sans jointure ni `Seq Scan` au moment de la requête — et sans consommer les ressources de la base partagée par tout le monde.

# Éviter de réindexer trois fois le même don

Indexer nos paiements réglait le problème de lecture. Restait un problème symétrique, côté écriture : un don, chez nous, ne s'écrit pas en une seule fois. Le traitement d'un paiement le sauvegarde plusieurs fois — à la création, puis au fil des étapes de validation et de rattachement à une commande. En pratique, un même paiement est sauvegardé environ trois fois pendant son traitement.

Par défaut, Wagtail réindexe un objet à chaque sauvegarde. Trois sauvegardes par don, ça fait trois réindexations pour le même objet. Sur un pic d'activité de plusieurs milliers de dons, par exemple pendant une grosse campagne, ça représente plusieurs milliers de réindexations superflues en quelques minutes — exactement au moment où la base partagée par toutes les associations est déjà la plus sollicitée.

On a donc désactivé la réindexation automatique de Wagtail et construit notre propre mécanisme, sur deux mixins :

- `IndexableMixin`, posé sur `Payment` et `Commitment`. Il coupe `search_auto_update`, ajoute un champ `needs_reindexing`, et le passe à `True` à la création ou dès qu'un champ indexé a réellement changé (on compare un instantané avant/après sauvegarde).
- `RelatedIndexableMixin`, posé sur `Contact`. Quand un champ indexé de `Contact` change (un email corrigé, par exemple), il retrouve les paiements et engagements liés et les marque comme nécessitant une réindexation.

À chaque fois qu'un objet est marqué, une tâche Celery unique est planifiée 20 minutes plus tard :

```python
def schedule_reindex_task_on_commit() -> None:
    """Planifie la tâche globale de réindexation 20 minutes dans le futur.
    Si une tâche est déjà planifiée dans le futur, elle est conservée telle quelle.
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

Le point important, c'est le `if` : si une tâche est déjà planifiée dans le futur, on ne fait rien. Que le don déclenche une, deux ou trois sauvegardes dans les 20 minutes qui suivent, une seule tâche de réindexation reste programmée.

La tâche elle-même parcourt chaque modèle indexable, ne récupère que les lignes marquées `needs_reindexing=True`, les traite par lots via `itertools.batched`, les envoie à l'index Postgres, puis retire le marqueur seulement sur les lignes traitées :

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

Avec ce mécanisme, un pic de plusieurs milliers de dons ne déclenche jamais plusieurs milliers de réindexations immédiates. Il déclenche une seule passe de réindexation, 20 minutes plus tard, qui traite en une fois tous les objets marqués entre-temps.

# En résumé

Le point de départ était un problème de performance au niveau des requêtes à la base de données : des recherches lentes, sur une instance PostgreSQL partagée par toutes nos associations (chacune isolée dans son propre schéma, mais toutes sur la même instance). La réponse tient en deux mouvements, tous les deux pensés pour ne pas ajouter de charge à cette base plutôt que pour la déplacer ailleurs.

Le premier, c'est déplacer le coût au bon endroit : Wagtail sait indexer, via `RelatedFields`, des champs portés par une table liée, et PostgreSQL sait ensuite les retrouver par un index plutôt que par un `Seq Scan`. On n'a eu besoin ni d'un moteur de recherche externe, ni du budget ni de l'équipe pour l'opérer.

Le second, c'est ne pas remplacer un problème de lecture par un problème d'écriture : réindexer à chaque sauvegarde aurait multiplié la charge sur cette même base à chaque pic de dons. Le batching à 20 minutes ramène ce coût à une seule passe, quel que soit le nombre de sauvegardes intervenues entre-temps.

Au final, la recherche est redevenue instantanée, sans service supplémentaire ni ligne de facture en plus, et sans faire porter aux autres associations le coût des pics de dons de l'une d'entre elles.
