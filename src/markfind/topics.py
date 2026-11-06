"""Group pages into topics by the words they use.

Each page becomes a TF-IDF vector over word stems (made by ``Store.topics``),
and the vectors are clustered with spherical k-means: like k-means, but on
unit vectors compared by cosine similarity, which suits text. Everything here
is plain Python over sparse dicts; there are no dependencies.

k-means can settle on a poor grouping depending on where it starts, so it
runs from RESTARTS different starting points and keeps the grouping whose
pages are closest to their topics. Each run compares a page only with the
topic centres that share one of its terms, found through an inverted index,
which keeps thousands of pages to a few seconds.

When markfind picks the number of topics itself, it's only an estimate, and
guessing high splits a subject in two. So afterwards, topics whose centres
are more than MERGE_SIMILARITY alike are merged, and k-means runs once more
from the merged centres. (On test collections, halves of one subject were
0.3 to 1.0 alike, and different subjects at most 0.12.)

The clustering is deterministic: the same pages give the same topics.
"""

from __future__ import annotations

import heapq
import math
import random
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

# Terms per page vector: a page's most telling words are enough to place it,
# and keeping vectors short keeps clustering fast.
TERMS_PER_PAGE = 25
# A term on more than this share of pages can't tell most of them apart. (A
# subject's words can be on a third of pages when there are only three.)
MAX_SHARE = 0.5
# Pages less similar than this to their topic's centre are left unsorted.
MIN_SIMILARITY = 0.1
MIN_SIZE = 3
MAX_TOPICS = 40
ITERATIONS = 25
RESTARTS = 4
MERGE_SIMILARITY = 0.25
LABEL_TERMS = 3

Vector = dict[str, float]


@dataclass(frozen=True)
class Clusters:
    groups: list[list[int]]  # page indexes per topic, biggest topic first
    labels: list[list[str]]  # each topic's most telling terms
    other: list[int]  # pages that fit no topic


def vectors(pages: Sequence[Counter[str]]) -> list[Vector]:
    """Unit TF-IDF vectors, each with at most TERMS_PER_PAGE terms.

    ``pages`` holds each page's term counts. Terms on only one page can't
    group pages, and terms on more than MAX_SHARE of them describe the
    whole collection, so both are left out.
    """
    count = len(pages)
    df: Counter[str] = Counter()
    for terms in pages:
        df.update(terms.keys())
    limit = max(2, MAX_SHARE * count)
    idf = {term: math.log(count / n) for term, n in df.items() if 2 <= n <= limit}
    result = []
    for terms in pages:
        weights = [
            (-(1 + math.log(n)) * idf[term], term) for term, n in terms.items() if term in idf
        ]
        # The heaviest terms; ties go to the alphabetically first, for stable results.
        top = [(t, -w) for w, t in heapq.nsmallest(TERMS_PER_PAGE, weights)]
        norm = math.sqrt(sum(w * w for _, w in top))
        result.append({term: w / norm for term, w in top} if norm else {})
    return result


def _centre(members: list[Vector]) -> Vector:
    total: Counter[str] = Counter()
    for vector in members:
        total.update(vector)
    norm = math.sqrt(sum(w * w for w in total.values()))
    return {term: w / norm for term, w in total.items()} if norm else {}


def _assign(
    vecs: Sequence[Vector], usable: list[int], centres: list[Vector]
) -> dict[int, tuple[int, float]]:
    """Each page's closest centre and its similarity.

    Pages that share no term with any centre are left out.
    """
    index: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for c, centre in enumerate(centres):
        for term, weight in centre.items():
            index[term].append((c, weight))
    result = {}
    for i in usable:
        scores: dict[int, float] = defaultdict(float)
        for term, weight in vecs[i].items():
            for c, centre_weight in index.get(term, ()):
                scores[c] += weight * centre_weight
        if scores:
            c = max(scores, key=lambda c: (scores[c], -c))
            result[i] = (c, scores[c])
    return result


def _similarities(vector: Vector, postings: dict[str, list[tuple[int, float]]]) -> dict[int, float]:
    """Similarity of ``vector`` to every page sharing a term with it, by position in ``usable``."""
    sims: dict[int, float] = defaultdict(float)
    for term, weight in vector.items():
        for j, page_weight in postings.get(term, ()):
            sims[j] += weight * page_weight
    return sims


def _kmeans(
    vecs: Sequence[Vector],
    usable: list[int],
    postings: dict[str, list[tuple[int, float]]],
    k: int,
    seed: int,
) -> tuple[list[Vector], dict[int, tuple[int, float]]]:
    """One run of spherical k-means: the centres, and each page's (centre, similarity)."""
    # k-means++ seeding: each next centre is picked with probability growing
    # with its distance from the centres so far, so they start spread out.
    rnd = random.Random(seed)
    centres = [vecs[usable[rnd.randrange(len(usable))]]]
    best = [0.0] * len(usable)
    for j, sim in _similarities(centres[0], postings).items():
        best[j] = sim
    while len(centres) < k:
        distance = [max(0.0, 1 - b) for b in best]
        total = sum(distance)
        if total == 0:
            break
        pick = rnd.random() * total
        chosen = usable[-1]  # if rounding leaves `pick` above zero
        for i, d in zip(usable, distance, strict=True):
            pick -= d
            if pick <= 0:
                chosen = i
                break
        centres.append(vecs[chosen])
        for j, sim in _similarities(vecs[chosen], postings).items():
            best[j] = max(best[j], sim)
    return _lloyd(vecs, usable, centres)


def _lloyd(
    vecs: Sequence[Vector], usable: list[int], centres: list[Vector]
) -> tuple[list[Vector], dict[int, tuple[int, float]]]:
    """k-means iterations from ``centres``: assign pages, recompute centres, until stable."""
    assignment: dict[int, tuple[int, float]] = {}
    for _ in range(ITERATIONS):
        new = _assign(vecs, usable, centres)
        if {i: c for i, (c, _) in new.items()} == {i: c for i, (c, _) in assignment.items()}:
            assignment = new
            break
        assignment = new
        members: list[list[Vector]] = [[] for _ in centres]
        for i, (c, _) in assignment.items():
            members[c].append(vecs[i])
        centres = [_centre(m) if m else centres[c] for c, m in enumerate(members)]
    return centres, assignment


def _merged(
    vecs: Sequence[Vector], centres: list[Vector], assignment: dict[int, tuple[int, float]]
) -> list[Vector]:
    """Centres after merging topics more than MERGE_SIMILARITY alike."""
    members: list[list[int]] = [[] for _ in centres]
    for i, (c, _) in assignment.items():
        members[c].append(i)
    live = [c for c, m in enumerate(members) if m]
    parent = {c: c for c in live}

    def root(c: int) -> int:
        while parent[c] != c:
            parent[c] = parent[parent[c]]
            c = parent[c]
        return c

    for n, a in enumerate(live):
        for b in live[n + 1 :]:
            similarity = sum(w * centres[b].get(t, 0.0) for t, w in centres[a].items())
            if similarity > MERGE_SIMILARITY:
                parent[root(b)] = root(a)
    groups: dict[int, list[Vector]] = defaultdict(list)
    for c in live:
        groups[root(c)].extend(vecs[i] for i in members[c])
    return [_centre(group) for _, group in sorted(groups.items())]


def default_count(pages: int) -> int:
    """How many topics to look for: about sqrt(pages / 2)."""
    return max(1, min(MAX_TOPICS, round(math.sqrt(pages / 2))))


def cluster(
    vecs: Sequence[Vector],
    count: int | None = None,
    min_size: int = MIN_SIZE,
    min_similarity: float = MIN_SIMILARITY,
    seed: int = 0,
) -> Clusters:
    """Group vectors into about ``count`` topics (default: ``default_count``).

    Topics smaller than ``min_size`` are dissolved, and pages less similar
    than ``min_similarity`` to their topic go to ``other``.
    """
    usable = [i for i, v in enumerate(vecs) if v]
    if not usable:
        return Clusters([], [], list(range(len(vecs))))
    k = min(count or default_count(len(usable)), len(usable))

    postings: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for j, i in enumerate(usable):
        for term, weight in vecs[i].items():
            postings[term].append((j, weight))
    # Keep the run whose pages are, in total, closest to their topics.
    runs = [_kmeans(vecs, usable, postings, k, seed + n) for n in range(RESTARTS)]
    centres, assignment = max(
        runs, key=lambda run: sum(similarity for _, similarity in run[1].values())
    )
    if count is None:
        centres, assignment = _lloyd(vecs, usable, _merged(vecs, centres, assignment))

    groups: list[list[tuple[float, int]]] = [[] for _ in centres]
    other = [i for i in range(len(vecs)) if i not in assignment]
    for i, (c, similarity) in assignment.items():
        if similarity >= min_similarity:
            groups[c].append((similarity, i))
        else:
            other.append(i)
    kept = []
    for c, members in enumerate(groups):
        if len(members) >= min_size:
            # Most typical pages first.
            kept.append(
                ([i for _, i in sorted(members, key=lambda m: (-m[0], m[1]))], _label(centres[c]))
            )
        else:
            other.extend(i for _, i in members)
    kept.sort(key=lambda item: (-len(item[0]), item[1]))
    return Clusters([g for g, _ in kept], [label for _, label in kept], sorted(other))


def _label(centre: Vector) -> list[str]:
    return [t for t, _ in sorted(centre.items(), key=lambda item: (-item[1], item[0]))][
        :LABEL_TERMS
    ]
