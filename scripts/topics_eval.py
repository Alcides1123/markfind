#!/usr/bin/env python3
"""How well does `markfind topics` recover real subjects?

    python scripts/topics_eval.py bookmarks tuning > tuning.html
    markfind --db tuning.sqlite import tuning.html
    markfind --db tuning.sqlite fetch
    python scripts/topics_eval.py score tuning.sqlite

The bookmark folders are the right answers. `score` works on any database whose
folders are subjects, and it never writes to the database.

The settings in topics.py were chosen on `tuning` and `second`. `validation` has
other subjects and sites, some of them deliberately close, and was scored once,
after they were chosen. It has been looked at now, so if you change the settings,
judge them on a new set. Web pages change, so scores drift a little over time.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from html import escape
from pathlib import Path

from markfind.store import Store

TUNING = {
    "baking": [
        "https://en.wikipedia.org/wiki/Bread",
        "https://en.wikipedia.org/wiki/Sourdough",
        "https://en.wikipedia.org/wiki/Baking",
        "https://en.wikipedia.org/wiki/Croissant",
        "https://en.wikipedia.org/wiki/Yeast",
        "https://en.wikipedia.org/wiki/Flour",
        "https://en.wikipedia.org/wiki/Pastry",
        "https://en.wikipedia.org/wiki/Cake",
        "https://en.wikipedia.org/wiki/Dough",
        "https://en.wikipedia.org/wiki/Baguette",
    ],
    "astronomy": [
        "https://en.wikipedia.org/wiki/Telescope",
        "https://en.wikipedia.org/wiki/Galaxy",
        "https://en.wikipedia.org/wiki/Black_hole",
        "https://en.wikipedia.org/wiki/Supernova",
        "https://en.wikipedia.org/wiki/Nebula",
        "https://en.wikipedia.org/wiki/Exoplanet",
        "https://en.wikipedia.org/wiki/Milky_Way",
        "https://en.wikipedia.org/wiki/Comet",
        "https://en.wikipedia.org/wiki/Hubble_Space_Telescope",
        "https://en.wikipedia.org/wiki/Neutron_star",
    ],
    "medicine": [
        "https://en.wikipedia.org/wiki/Vaccine",
        "https://en.wikipedia.org/wiki/Antibiotic",
        "https://en.wikipedia.org/wiki/Diabetes",
        "https://en.wikipedia.org/wiki/Hypertension",
        "https://en.wikipedia.org/wiki/Influenza",
        "https://en.wikipedia.org/wiki/Chemotherapy",
        "https://en.wikipedia.org/wiki/Asthma",
        "https://en.wikipedia.org/wiki/Stroke",
        "https://en.wikipedia.org/wiki/Pneumonia",
        "https://en.wikipedia.org/wiki/Malaria",
    ],
    "football": [
        "https://en.wikipedia.org/wiki/FIFA_World_Cup",
        "https://en.wikipedia.org/wiki/Association_football",
        "https://en.wikipedia.org/wiki/Offside_(association_football)",
        "https://en.wikipedia.org/wiki/Premier_League",
        "https://en.wikipedia.org/wiki/UEFA_Champions_League",
        "https://en.wikipedia.org/wiki/Lionel_Messi",
        "https://en.wikipedia.org/wiki/Pel%C3%A9",
        "https://en.wikipedia.org/wiki/Goalkeeper_(association_football)",
        "https://en.wikipedia.org/wiki/Real_Madrid_CF",
        "https://en.wikipedia.org/wiki/FC_Barcelona",
    ],
    "roman_history": [
        "https://en.wikipedia.org/wiki/Roman_Empire",
        "https://en.wikipedia.org/wiki/Julius_Caesar",
        "https://en.wikipedia.org/wiki/Augustus",
        "https://en.wikipedia.org/wiki/Roman_Republic",
        "https://en.wikipedia.org/wiki/Punic_Wars",
        "https://en.wikipedia.org/wiki/Colosseum",
        "https://en.wikipedia.org/wiki/Nero",
        "https://en.wikipedia.org/wiki/Fall_of_the_Western_Roman_Empire",
        "https://en.wikipedia.org/wiki/Roman_legion",
        "https://en.wikipedia.org/wiki/Hadrian",
    ],
    "music": [
        "https://en.wikipedia.org/wiki/Piano",
        "https://en.wikipedia.org/wiki/Violin",
        "https://en.wikipedia.org/wiki/Symphony",
        "https://en.wikipedia.org/wiki/Ludwig_van_Beethoven",
        "https://en.wikipedia.org/wiki/Wolfgang_Amadeus_Mozart",
        "https://en.wikipedia.org/wiki/Johann_Sebastian_Bach",
        "https://en.wikipedia.org/wiki/Opera",
        "https://en.wikipedia.org/wiki/Orchestra",
        "https://en.wikipedia.org/wiki/Sonata",
        "https://en.wikipedia.org/wiki/Counterpoint",
    ],
    "search_engines": [
        "https://en.wikipedia.org/wiki/Okapi_BM25",
        "https://en.wikipedia.org/wiki/Tf%E2%80%93idf",
        "https://en.wikipedia.org/wiki/Inverted_index",
        "https://en.wikipedia.org/wiki/Search_engine",
        "https://en.wikipedia.org/wiki/PageRank",
        "https://en.wikipedia.org/wiki/Information_retrieval",
        "https://en.wikipedia.org/wiki/Web_crawler",
        "https://en.wikipedia.org/wiki/Precision_and_recall",
        "https://en.wikipedia.org/wiki/Stemming",
        "https://en.wikipedia.org/wiki/Full-text_search",
    ],
    "python_docs": [
        "https://docs.python.org/3/library/sqlite3.html",
        "https://docs.python.org/3/library/argparse.html",
        "https://docs.python.org/3/library/asyncio.html",
        "https://docs.python.org/3/library/json.html",
        "https://docs.python.org/3/library/re.html",
        "https://docs.python.org/3/library/pathlib.html",
        "https://docs.python.org/3/library/collections.html",
        "https://docs.python.org/3/library/itertools.html",
        "https://docs.python.org/3/library/functools.html",
        "https://docs.python.org/3/library/subprocess.html",
    ],
    "rust_book": [
        "https://doc.rust-lang.org/book/ch04-01-what-is-ownership.html",
        "https://doc.rust-lang.org/book/ch04-02-references-and-borrowing.html",
        "https://doc.rust-lang.org/book/ch10-02-traits.html",
        "https://doc.rust-lang.org/book/ch10-03-lifetime-syntax.html",
        "https://doc.rust-lang.org/book/ch06-01-defining-an-enum.html",
        "https://doc.rust-lang.org/book/ch08-01-vectors.html",
        "https://doc.rust-lang.org/book/ch13-01-closures.html",
        "https://doc.rust-lang.org/book/ch15-01-box.html",
        "https://doc.rust-lang.org/book/ch16-01-threads.html",
        "https://doc.rust-lang.org/book/ch09-02-recoverable-errors-with-result.html",
    ],
    "postgres": [
        "https://www.postgresql.org/docs/current/textsearch-intro.html",
        "https://www.postgresql.org/docs/current/indexes-types.html",
        "https://www.postgresql.org/docs/current/sql-select.html",
        "https://www.postgresql.org/docs/current/mvcc-intro.html",
        "https://www.postgresql.org/docs/current/wal-intro.html",
        "https://www.postgresql.org/docs/current/sql-createtable.html",
        "https://www.postgresql.org/docs/current/tutorial-join.html",
        "https://www.postgresql.org/docs/current/ddl-partitioning.html",
        "https://www.postgresql.org/docs/current/sql-explain.html",
        "https://www.postgresql.org/docs/current/transaction-iso.html",
    ],
    "web_http": [
        "https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Content-Security-Policy",
        "https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/CORS",
        "https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/Caching",
        "https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/Cookies",
        "https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Status",
        "https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/Overview",
        "https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Authorization",
        "https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Methods",
        "https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Strict-Transport-Security",
        "https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/Compression",
    ],
    "git": [
        "https://git-scm.com/book/en/v2/Git-Branching-Rebasing",
        "https://git-scm.com/book/en/v2/Git-Branching-Basic-Branching-and-Merging",
        "https://git-scm.com/book/en/v2/Git-Basics-Recording-Changes-to-the-Repository",
        "https://git-scm.com/book/en/v2/Git-Basics-Undoing-Things",
        "https://git-scm.com/book/en/v2/Git-Tools-Stashing-and-Cleaning",
        "https://git-scm.com/book/en/v2/Git-Tools-Rewriting-History",
        "https://git-scm.com/book/en/v2/Git-Internals-Git-Objects",
        "https://git-scm.com/book/en/v2/Git-Branching-Remote-Branches",
        "https://git-scm.com/book/en/v2/Git-Basics-Working-with-Remotes",
        "https://git-scm.com/book/en/v2/Git-Tools-Reset-Demystified",
    ],
}

SECOND = {
    "chess": [
        "https://en.wikipedia.org/wiki/Chess",
        "https://en.wikipedia.org/wiki/Sicilian_Defence",
        "https://en.wikipedia.org/wiki/Magnus_Carlsen",
        "https://en.wikipedia.org/wiki/Garry_Kasparov",
        "https://en.wikipedia.org/wiki/Chess_opening",
        "https://en.wikipedia.org/wiki/Endgame_tablebase",
        "https://en.wikipedia.org/wiki/Castling",
        "https://en.wikipedia.org/wiki/Queen%27s_Gambit",
        "https://en.wikipedia.org/wiki/Bobby_Fischer",
        "https://en.wikipedia.org/wiki/Chess_tournament",
    ],
    "volcanoes": [
        "https://en.wikipedia.org/wiki/Volcano",
        "https://en.wikipedia.org/wiki/Mount_Vesuvius",
        "https://en.wikipedia.org/wiki/Lava",
        "https://en.wikipedia.org/wiki/Magma",
        "https://en.wikipedia.org/wiki/Krakatoa",
        "https://en.wikipedia.org/wiki/Mount_St._Helens",
        "https://en.wikipedia.org/wiki/Pyroclastic_flow",
        "https://en.wikipedia.org/wiki/Caldera",
        "https://en.wikipedia.org/wiki/Stratovolcano",
        "https://en.wikipedia.org/wiki/Plate_tectonics",
    ],
    "economics": [
        "https://en.wikipedia.org/wiki/Inflation",
        "https://en.wikipedia.org/wiki/Gross_domestic_product",
        "https://en.wikipedia.org/wiki/Supply_and_demand",
        "https://en.wikipedia.org/wiki/Keynesian_economics",
        "https://en.wikipedia.org/wiki/Monetary_policy",
        "https://en.wikipedia.org/wiki/Recession",
        "https://en.wikipedia.org/wiki/Adam_Smith",
        "https://en.wikipedia.org/wiki/Central_bank",
        "https://en.wikipedia.org/wiki/Unemployment",
        "https://en.wikipedia.org/wiki/Comparative_advantage",
    ],
    "philosophy": [
        "https://en.wikipedia.org/wiki/Immanuel_Kant",
        "https://en.wikipedia.org/wiki/Plato",
        "https://en.wikipedia.org/wiki/Aristotle",
        "https://en.wikipedia.org/wiki/Existentialism",
        "https://en.wikipedia.org/wiki/Utilitarianism",
        "https://en.wikipedia.org/wiki/Epistemology",
        "https://en.wikipedia.org/wiki/Stoicism",
        "https://en.wikipedia.org/wiki/Friedrich_Nietzsche",
        "https://en.wikipedia.org/wiki/Ren%C3%A9_Descartes",
        "https://en.wikipedia.org/wiki/Ethics",
    ],
    "machine_learning": [
        "https://en.wikipedia.org/wiki/Machine_learning",
        "https://en.wikipedia.org/wiki/Neural_network_(machine_learning)",
        "https://en.wikipedia.org/wiki/Backpropagation",
        "https://en.wikipedia.org/wiki/Random_forest",
        "https://en.wikipedia.org/wiki/Support_vector_machine",
        "https://en.wikipedia.org/wiki/Gradient_descent",
        "https://en.wikipedia.org/wiki/Overfitting",
        "https://en.wikipedia.org/wiki/Reinforcement_learning",
        "https://en.wikipedia.org/wiki/Transformer_(deep_learning)",
        "https://en.wikipedia.org/wiki/Convolutional_neural_network",
    ],
    "coffee": [
        "https://en.wikipedia.org/wiki/Coffee",
        "https://en.wikipedia.org/wiki/Espresso",
        "https://en.wikipedia.org/wiki/Latte",
        "https://en.wikipedia.org/wiki/Coffee_bean",
        "https://en.wikipedia.org/wiki/Cappuccino",
        "https://en.wikipedia.org/wiki/Coffee_roasting",
        "https://en.wikipedia.org/wiki/Arabica_coffee",
        "https://en.wikipedia.org/wiki/French_press",
        "https://en.wikipedia.org/wiki/Coffeehouse",
        "https://en.wikipedia.org/wiki/Caff%C3%A8_mocha",
    ],
    "tea": [
        "https://en.wikipedia.org/wiki/Tea",
        "https://en.wikipedia.org/wiki/Green_tea",
        "https://en.wikipedia.org/wiki/Oolong",
        "https://en.wikipedia.org/wiki/Matcha",
        "https://en.wikipedia.org/wiki/Black_tea",
        "https://en.wikipedia.org/wiki/Chinese_tea_culture",
        "https://en.wikipedia.org/wiki/Japanese_tea_ceremony",
        "https://en.wikipedia.org/wiki/Earl_Grey_tea",
        "https://en.wikipedia.org/wiki/Masala_chai",
        "https://en.wikipedia.org/wiki/Darjeeling_tea",
    ],
    "javascript": [
        "https://developer.mozilla.org/en-US/docs/Web/JavaScript/Guide/Closures",
        "https://developer.mozilla.org/en-US/docs/Web/JavaScript/Guide/Functions",
        "https://developer.mozilla.org/en-US/docs/Web/JavaScript/Guide/Using_promises",
        "https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Array",
        "https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Map",
        "https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Operators/await",
        "https://developer.mozilla.org/en-US/docs/Web/JavaScript/Guide/Modules",
        "https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Promise",
        "https://developer.mozilla.org/en-US/docs/Web/JavaScript/Guide/Regular_expressions",
        "https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Classes",
    ],
    "sqlite_docs": [
        "https://sqlite.org/lang_select.html",
        "https://sqlite.org/lang_transaction.html",
        "https://sqlite.org/wal.html",
        "https://sqlite.org/queryplanner.html",
        "https://sqlite.org/lang_createindex.html",
        "https://sqlite.org/datatype3.html",
        "https://sqlite.org/lang_expr.html",
        "https://sqlite.org/pragma.html",
        "https://sqlite.org/foreignkeys.html",
        "https://sqlite.org/json1.html",
    ],
    "kubernetes": [
        "https://kubernetes.io/docs/concepts/workloads/pods/",
        "https://kubernetes.io/docs/concepts/services-networking/service/",
        "https://kubernetes.io/docs/concepts/workloads/controllers/deployment/",
        "https://kubernetes.io/docs/concepts/configuration/configmap/",
        "https://kubernetes.io/docs/concepts/storage/persistent-volumes/",
        "https://kubernetes.io/docs/concepts/overview/components/",
        "https://kubernetes.io/docs/concepts/security/secrets-good-practices/",
        "https://kubernetes.io/docs/concepts/workloads/controllers/daemonset/",
        "https://kubernetes.io/docs/concepts/services-networking/ingress/",
        "https://kubernetes.io/docs/concepts/scheduling-eviction/taint-and-toleration/",
    ],
    "django": [
        "https://docs.djangoproject.com/en/stable/topics/db/models/",
        "https://docs.djangoproject.com/en/stable/topics/db/queries/",
        "https://docs.djangoproject.com/en/stable/topics/http/views/",
        "https://docs.djangoproject.com/en/stable/topics/http/urls/",
        "https://docs.djangoproject.com/en/stable/topics/forms/index/",
        "https://docs.djangoproject.com/en/stable/topics/templates/",
        "https://docs.djangoproject.com/en/stable/topics/auth/default/",
        "https://docs.djangoproject.com/en/stable/topics/testing/overview/",
        "https://docs.djangoproject.com/en/stable/topics/migrations/",
        "https://docs.djangoproject.com/en/stable/topics/cache/",
    ],
    "birds": [
        "https://en.wikipedia.org/wiki/Bird",
        "https://en.wikipedia.org/wiki/Owl",
        "https://en.wikipedia.org/wiki/Penguin",
        "https://en.wikipedia.org/wiki/Bird_migration",
        "https://en.wikipedia.org/wiki/Eagle",
        "https://en.wikipedia.org/wiki/Hummingbird",
        "https://en.wikipedia.org/wiki/Crow",
        "https://en.wikipedia.org/wiki/Albatross",
        "https://en.wikipedia.org/wiki/Parrot",
        "https://en.wikipedia.org/wiki/Feather",
    ],
}

VALIDATION = {
    "photography": [
        "https://en.wikipedia.org/wiki/Photography",
        "https://en.wikipedia.org/wiki/Aperture",
        "https://en.wikipedia.org/wiki/Shutter_speed",
        "https://en.wikipedia.org/wiki/Film_speed",
        "https://en.wikipedia.org/wiki/Camera_lens",
        "https://en.wikipedia.org/wiki/Exposure_(photography)",
        "https://en.wikipedia.org/wiki/Depth_of_field",
        "https://en.wikipedia.org/wiki/Single-lens_reflex_camera",
        "https://en.wikipedia.org/wiki/Ansel_Adams",
        "https://en.wikipedia.org/wiki/Photographic_film",
    ],
    "dinosaurs": [
        "https://en.wikipedia.org/wiki/Dinosaur",
        "https://en.wikipedia.org/wiki/Tyrannosaurus",
        "https://en.wikipedia.org/wiki/Triceratops",
        "https://en.wikipedia.org/wiki/Velociraptor",
        "https://en.wikipedia.org/wiki/Stegosaurus",
        "https://en.wikipedia.org/wiki/Sauropoda",
        "https://en.wikipedia.org/wiki/Cretaceous",
        "https://en.wikipedia.org/wiki/Jurassic",
        "https://en.wikipedia.org/wiki/Archaeopteryx",
        "https://en.wikipedia.org/wiki/Theropoda",
    ],
    "climate": [
        "https://en.wikipedia.org/wiki/Climate_change",
        "https://en.wikipedia.org/wiki/Greenhouse_gas",
        "https://en.wikipedia.org/wiki/Carbon_dioxide_in_Earth%27s_atmosphere",
        "https://en.wikipedia.org/wiki/Paris_Agreement",
        "https://en.wikipedia.org/wiki/Sea_level_rise",
        "https://en.wikipedia.org/wiki/El_Ni%C3%B1o%E2%80%93Southern_Oscillation",
        "https://en.wikipedia.org/wiki/Ice_core",
        "https://en.wikipedia.org/wiki/Intergovernmental_Panel_on_Climate_Change",
        "https://en.wikipedia.org/wiki/Ocean_acidification",
        "https://en.wikipedia.org/wiki/Effects_of_climate_change",
    ],
    "french_revolution": [
        "https://en.wikipedia.org/wiki/French_Revolution",
        "https://en.wikipedia.org/wiki/Maximilien_Robespierre",
        "https://en.wikipedia.org/wiki/Reign_of_Terror",
        "https://en.wikipedia.org/wiki/Louis_XVI",
        "https://en.wikipedia.org/wiki/Storming_of_the_Bastille",
        "https://en.wikipedia.org/wiki/Marie_Antoinette",
        "https://en.wikipedia.org/wiki/Jacobin",
        "https://en.wikipedia.org/wiki/Estates_General_of_1789",
        "https://en.wikipedia.org/wiki/Napoleon",
        "https://en.wikipedia.org/wiki/National_Convention",
    ],
    "sailing": [
        "https://en.wikipedia.org/wiki/Sailing",
        "https://en.wikipedia.org/wiki/Sailboat",
        "https://en.wikipedia.org/wiki/Spinnaker",
        "https://en.wikipedia.org/wiki/Keel",
        "https://en.wikipedia.org/wiki/Regatta",
        "https://en.wikipedia.org/wiki/America%27s_Cup",
        "https://en.wikipedia.org/wiki/Mainsail",
        "https://en.wikipedia.org/wiki/Rigging",
        "https://en.wikipedia.org/wiki/Points_of_sail",
        "https://en.wikipedia.org/wiki/Catamaran",
    ],
    "cheese": [
        "https://en.wikipedia.org/wiki/Cheese",
        "https://en.wikipedia.org/wiki/Cheddar_cheese",
        "https://en.wikipedia.org/wiki/Brie",
        "https://en.wikipedia.org/wiki/Parmesan",
        "https://en.wikipedia.org/wiki/Rennet",
        "https://en.wikipedia.org/wiki/Roquefort",
        "https://en.wikipedia.org/wiki/Gouda_cheese",
        "https://en.wikipedia.org/wiki/Mozzarella",
        "https://en.wikipedia.org/wiki/Camembert_(cheese)",
        "https://en.wikipedia.org/wiki/Feta",
    ],
    "wine": [
        "https://en.wikipedia.org/wiki/Wine",
        "https://en.wikipedia.org/wiki/Cabernet_Sauvignon",
        "https://en.wikipedia.org/wiki/Merlot",
        "https://en.wikipedia.org/wiki/Chardonnay",
        "https://en.wikipedia.org/wiki/Pinot_noir",
        "https://en.wikipedia.org/wiki/Winemaking",
        "https://en.wikipedia.org/wiki/Terroir",
        "https://en.wikipedia.org/wiki/Bordeaux_wine",
        "https://en.wikipedia.org/wiki/Champagne",
        "https://en.wikipedia.org/wiki/Sommelier",
    ],
    "nodejs": [
        "https://nodejs.org/api/fs.html",
        "https://nodejs.org/api/http.html",
        "https://nodejs.org/api/stream.html",
        "https://nodejs.org/api/events.html",
        "https://nodejs.org/api/buffer.html",
        "https://nodejs.org/api/path.html",
        "https://nodejs.org/api/crypto.html",
        "https://nodejs.org/api/child_process.html",
        "https://nodejs.org/api/worker_threads.html",
        "https://nodejs.org/api/net.html",
    ],
    "typescript": [
        "https://www.typescriptlang.org/docs/handbook/2/basic-types.html",
        "https://www.typescriptlang.org/docs/handbook/2/everyday-types.html",
        "https://www.typescriptlang.org/docs/handbook/2/narrowing.html",
        "https://www.typescriptlang.org/docs/handbook/2/functions.html",
        "https://www.typescriptlang.org/docs/handbook/2/objects.html",
        "https://www.typescriptlang.org/docs/handbook/2/generics.html",
        "https://www.typescriptlang.org/docs/handbook/2/classes.html",
        "https://www.typescriptlang.org/docs/handbook/2/modules.html",
        "https://www.typescriptlang.org/docs/handbook/2/conditional-types.html",
        "https://www.typescriptlang.org/docs/handbook/2/mapped-types.html",
    ],
    "nginx": [
        "https://nginx.org/en/docs/http/ngx_http_core_module.html",
        "https://nginx.org/en/docs/http/ngx_http_proxy_module.html",
        "https://nginx.org/en/docs/http/ngx_http_ssl_module.html",
        "https://nginx.org/en/docs/http/ngx_http_upstream_module.html",
        "https://nginx.org/en/docs/http/ngx_http_rewrite_module.html",
        "https://nginx.org/en/docs/http/ngx_http_gzip_module.html",
        "https://nginx.org/en/docs/http/ngx_http_log_module.html",
        "https://nginx.org/en/docs/http/load_balancing.html",
        "https://nginx.org/en/docs/beginners_guide.html",
        "https://nginx.org/en/docs/http/configuring_https_servers.html",
    ],
    "docker": [
        "https://docs.docker.com/get-started/docker-overview/",
        "https://docs.docker.com/engine/storage/volumes/",
        "https://docs.docker.com/engine/network/",
        "https://docs.docker.com/build/concepts/dockerfile/",
        "https://docs.docker.com/compose/intro/compose-application-model/",
        "https://docs.docker.com/reference/dockerfile/",
        "https://docs.docker.com/engine/security/",
        "https://docs.docker.com/engine/containers/resource_constraints/",
        "https://docs.docker.com/build/building/multi-stage/",
        "https://docs.docker.com/engine/storage/bind-mounts/",
    ],
    "redis": [
        "https://redis.io/docs/latest/develop/data-types/strings/",
        "https://redis.io/docs/latest/develop/data-types/lists/",
        "https://redis.io/docs/latest/develop/data-types/hashes/",
        "https://redis.io/docs/latest/develop/data-types/sets/",
        "https://redis.io/docs/latest/develop/data-types/streams/",
        "https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/",
        "https://redis.io/docs/latest/operate/oss_and_stack/management/replication/",
        "https://redis.io/docs/latest/develop/interact/transactions/",
        "https://redis.io/docs/latest/develop/interact/pubsub/",
        "https://redis.io/docs/latest/operate/oss_and_stack/management/sentinel/",
    ],
}

SETS = {"tuning": TUNING, "second": SECOND, "validation": VALIDATION}


def bookmarks_html(corpus: Mapping[str, Sequence[str]]) -> str:
    """A bookmark export with one folder per subject."""
    lines = ["<!DOCTYPE NETSCAPE-Bookmark-file-1>", "<DL><p>"]
    for subject, urls in corpus.items():
        lines += [f"<DT><H3>{subject}</H3><DL><p>"]
        for url in urls:
            title = url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".html").replace("_", " ")
            lines.append(f'<DT><A HREF="{escape(url)}">{escape(title)}</A>')
        lines.append("</DL><p>")
    lines.append("</DL><p>")
    return "\n".join(lines) + "\n"


def _pairs(n: int) -> int:
    return n * (n - 1) // 2


def score(groups: Sequence[Sequence[str]], other: Sequence[str]) -> tuple[float, float]:
    """(adjusted Rand index, purity) of groups of page labels.

    The index is 1 for a perfect grouping and about 0 for a random one. Pages in
    ``other`` count as being alone. Purity is the share of grouped pages that
    belong to their group's largest subject.
    """
    clusters = [Counter(group) for group in groups] + [Counter([label]) for label in other]
    subjects = Counter(label for cluster in clusters for label in cluster.elements())
    together = sum(_pairs(n) for cluster in clusters for n in cluster.values())
    grouped = sum(_pairs(sum(cluster.values())) for cluster in clusters)
    same_subject = sum(_pairs(n) for n in subjects.values())
    total = _pairs(sum(subjects.values()))
    expected = grouped * same_subject / total if total else 0.0
    best = (grouped + same_subject) / 2
    index = (together - expected) / (best - expected) if best != expected else 1.0
    placed = sum(len(group) for group in groups)
    largest = sum(Counter(group).most_common(1)[0][1] for group in groups if group)
    return index, largest / placed if placed else 0.0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate markfind topics on real pages.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("bookmarks", help="print a set as a bookmark export").add_argument(
        "set", choices=SETS
    )
    commands.add_parser("score", help="score a fetched database").add_argument("db", type=Path)
    args = parser.parse_args(argv)

    if args.command == "bookmarks":
        print(bookmarks_html(SETS[args.set]), end="")
        return 0

    if not args.db.is_file():
        print(f"error: no database at {args.db}", file=sys.stderr)
        return 2
    with Store(args.db, readonly=True) as store:
        found = store.topics()
    groups = [[entry.folder for entry in topic.entries] for topic in found.topics]
    other = [entry.folder for entry in found.other]
    for topic, group in zip(found.topics, groups, strict=True):
        print(f"{' · '.join(topic.label)}: {dict(Counter(group))}")
    print(f"no clear topic: {dict(Counter(other))}")
    index, purity = score(groups, other)
    print(f"{len(groups)} topics, adjusted Rand index {index:.3f}, purity {purity:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
