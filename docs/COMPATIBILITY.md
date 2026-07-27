---
title: "Compatibilité qBittorrent"
description: "Ce que qbit-ops sait faire, sur quelles versions, et ce qui est réellement prouvé — politique fondée sur la Web API"
status: draft
---

# 🔗 Compatibilité qBittorrent

> **Statut : `draft`.** Ce document définit la **politique** de
> compatibilité et l'état des preuves au 2026-07-27. Il ne fixe
> volontairement **aucune borne de version définitive** : la matrice
> Docker (§1) a exercé quatre versions **précises**, chacune identifiée
> par son digest d'image exact — ce n'est pas une plage de versions
> supportées, et ce document ne publie toujours aucune telle plage.
> Formulation à utiliser : « container integration tested against exact
> versions: 4.6.7, 5.0.0, 5.1.4 and 5.2.3 » — jamais « compatible with
> qBittorrent 4.6–5.2 ».

---

## 1. ⚠️ État des preuves — à lire en premier

| Question | Réponse au 2026-07-27 |
|---|---|
| Une version de qBittorrent est-elle testée par `qbit-ops` ? | **Oui, quatre** — `v4.6.7` (Web API 2.9.3), `v5.0.0` (2.11.2), `v5.1.4` (2.11.4), `v5.2.3` (2.15.1, version stable courante au 2026-07-27), chacune contre un conteneur Docker jetable réel et identifié par son digest d'image exact (`tests/integration/qbittorrent-matrix.toml`). |
| Y a-t-il un test contre une instance réelle ? | **Oui** — `make test-qbit-matrix` / `make test-qbit-version QBIT_MATRIX_ID=<id>` (`tests/integration/`), scénarios de lecture, mutations à bas risque et mutations de tracker, tous exécutés en direct. |
| Y a-t-il des fixtures issues de réponses authentiques ? | **Oui** — `tests/compatibility/fixtures/captured-container/<matrix-id>/`, capturées et sanitisées par `tests/integration/_capture.py`, avec provenance complète (image, digest, version observée). |
| La CI exerce-t-elle qBittorrent ? | **Non pour `make ci`** (inchangé : lint, Pyright, pytest, `--help`, aucun Docker). **Oui pour un workflow séparé** (`.github/workflows/qbittorrent-matrix.yml`), déclenché manuellement ou de façon hebdomadaire, jamais sur chaque pull request. |
| Que valent les tests existants (hors matrice Docker) ? | Ils prouvent la **logique** de `qbit-ops` (filtres, plans, sûreté, non-fuite de secrets), contre un double écrit à la main ou des fixtures `synthetic`/`official-example`. Ils ne prouvent rien sur une instance réelle à eux seuls. |
| `auth_log_in()` est-il testé ? | **Oui, deux fois** : contre le harnais HTTP hermétique (`tests/test_qbit_library_http_boundary.py`, bibliothèque réelle + réponses simulées) et contre un conteneur qBittorrent réel (`tests/integration/`, chaque scénario commence par un login réel). |

La mention actuelle *« qbit-ops has been validated against qBittorrent
4.x and 5.x »* (`src/qbit_ops/doctor.py`, `docs/COMMANDS.md`) reste une
**assertion déclarative distincte** de ce que ce document peut
désormais justifier : la matrice Docker prouve quatre versions
**précises**, pas « toute version 4.x/5.x ». Elle est conservée telle
quelle dans le code tant que la phase compatibilité `doctor` (hors
périmètre ici) n'a pas remplacé le modèle.

---

## 2. 🧭 Pourquoi la Web API, et non la version applicative

`qbit-ops` interroge deux versions et n'en utilise réellement aucune pour
décider :

| Endpoint | Utilisation actuelle |
|---|---|
| `GET /api/v2/app/version` | affichée ; **seule** base du check `COMPAT002` (`major in {4, 5}`) |
| `GET /api/v2/app/webapiVersion` | affichée, exportée — **aucune décision n'en dépend** |

Or c'est la **version Web API** qui détermine ce qui est réellement
disponible :

- `qbittorrent-api` lui-même bascule `pause`→`stop` et `resume`→`start`
  au seuil **Web API 2.11.0**, pas sur la version applicative ;
- les planchers de version déclarés par la bibliothèque
  (`version_introduced`) sont tous exprimés en Web API ;
- une même version applicative peut exposer des Web API différentes selon
  la construction.

**Décision de politique** : la compatibilité de `qbit-ops` se définit par
**capacités adossées à des versions Web API**. La version applicative
reste collectée et affichée, à titre informatif uniquement.

---

## 3. 🧱 Capacités

`qbit-ops` n'utilise que 13 méthodes, regroupables en cinq capacités.

| Capacité | Endpoints | Commandes concernées | Plancher Web API déclaré par `qbittorrent-api` |
|---|---|---|---|
| `AUTH` | `auth/login` | toutes | aucun déclaré |
| `READ_INSTANCE` | `app/version`, `app/webapiVersion`, `transfer/info` | `status`, `doctor`, `tui`, `backup export` | aucun déclaré |
| `READ_TORRENTS` | `torrents/info` | quasi toutes | aucun déclaré |
| `READ_TRACKERS` | `torrents/trackers` | `trackers *`, `torrents inspect`, `explain`, `backup export` | aucun déclaré |
| `MUTATE_TORRENT_STATE` | `torrents/stop`\|`pause`, `torrents/start`\|`resume`, `torrents/reannounce` | `torrents pause/resume/start/reannounce`, actions TUI | `reannounce` : **2.0.2** ; bascule stop/start : **2.11.0** |
| `MUTATE_TRACKERS` | `torrents/addTrackers`, `torrents/editTracker`, `torrents/removeTrackers` | `trackers add-if-present/remove/replace/replace-passkey` | `editTracker`/`removeTrackers` : **2.2.0** ; `addTrackers` : aucun déclaré |

« Aucun déclaré » signifie exactement cela : la bibliothèque n'annonce pas
de plancher. Ce n'est **pas** une affirmation que l'endpoint existe depuis
toujours — c'est une absence d'information, à combler par les phases 5 et
6, pas par une valeur retenue de mémoire.

`GET /api/v2/app/buildInfo` (plancher déclaré **2.3**) n'est **jamais**
appelé par `qbit-ops` : aucune information de build n'est collectée,
affichée ni exportée.

---

## 4. 🏷️ Paliers de compatibilité

Quatre paliers, définis par **ce qui a été exécuté**, jamais par une
impression.

### `TESTED`

Une version Web API pour laquelle **chaque capacité de §3 a été exercée
contre une instance réelle** dans la matrice d'intégration CI, sur au
moins une exécution passante.

- Effet `doctor` : `pass`.
- Effet mutation : autorisée sans avertissement.
- **Aucune version n'a ce palier aujourd'hui.**

### `SUPPORTED_UNTESTED`

Une version Web API **comprise dans une plage bornée par deux versions
`TESTED`**, ou exercée par fixtures uniquement, sans qu'aucune capacité
n'ait été observée en échec.

- Effet `doctor` : `warning`, avec un message précisant que la version se
  situe dans une plage supportée mais non exercée directement.
- Effet mutation : autorisée, sans blocage.
- Effet lecture : nominal.

### `UNTESTED_NEWER`

Une version Web API **strictement supérieure** à la plus haute version
`TESTED`, ou dont la chaîne de version n'est pas analysable.

- Effet `doctor` : `warning`.
- Effet mutation : **autorisée**, avec avertissement.
- Justification : `qbit-ops` ne fabrique pas une incompatibilité qu'il n'a
  pas constatée. Bloquer sur « plus récent que ce que je connais »
  transformerait chaque publication amont en panne, ce qui contredit
  `PHILOSOPHY.md` §2 (« ne pas deviner ») autant que le ferait une
  autorisation aveugle : la position honnête est d'avertir.

### `UNSUPPORTED`

Une version Web API pour laquelle une capacité **requise par la commande
demandée** est absente ou a été observée en échec.

- Effet `doctor` : `fail`.
- Effet mutation : **refusée**, avec un message actionnable nommant la
  capacité manquante et la version minimale connue.
- Effet lecture : les commandes en lecture seule restent au
  **mieux-effort** — elles s'exécutent, dégradent proprement ce qu'elles
  ne peuvent pas collecter et le signalent, plutôt que de refuser.

### Tableau de synthèse

| Palier | `doctor` | Mutations | Lectures |
|---|---|---|---|
| `TESTED` | `pass` | autorisées | nominales |
| `SUPPORTED_UNTESTED` | `warning` | autorisées | nominales |
| `UNTESTED_NEWER` | `warning` | autorisées **avec avertissement** | nominales |
| `UNSUPPORTED` | `fail` | **refusées** | mieux-effort, dégradation signalée |

---

## 5. 🧪 Ce qu'il faut exercer, et comment

### 5.1 Par fixtures (phase 5 — hors ligne, dans `make check`)

Versions choisies parce qu'elles correspondent à un **branchement réel**
du code ou de la bibliothèque, pas parce qu'elles sont populaires :

| Web API | Motif |
|---|---|
| `2.8.x` | états `paused*`, endpoints `pause`/`resume` |
| `2.9.3` | valeur codée en dur dans le double de test actuel |
| `2.11.0` | **seuil exact** de bascule `stop`/`start` dans `qbittorrent-api` |
| `2.11.x` post-seuil | états `stopped*` |
| la plus récente disponible | détection de dérive de champs |

Contrainte non négociable : chaque fixture doit provenir d'une **capture
réelle** ou d'une source amont citable. Une fixture écrite à la main
reproduirait exactement le défaut du double actuel.

### 5.2 Par intégration réelle (job CI séparé, Docker) — livré 2026-07-27, étendu 2026-07-27

| Identifiant matrice | Image (digest exact) | Version observée | Web API observée | Motif |
|---|---|---|---|---|
| `qbit-4.6.7` | `linuxserver/qbittorrent:4.6.7` (`sha256:55f15d44...`) | `v4.6.7` | `2.9.3` | dernier 4.6.x maintenu, endpoints `pause`/`resume` |
| `qbit-5.0.0` | `linuxserver/qbittorrent:5.0.0` (`sha256:d01b1df5...`) | `v5.0.0` | `2.11.2` | première 5.0.x, au-dessus du seuil de bascule `stop`/`start` |
| `qbit-5.1.4` | `linuxserver/qbittorrent:5.1.4` (`sha256:c9990949...`) | `v5.1.4` | `2.11.4` | dernier 5.1.x maintenu |
| `qbit-5.2.3` | `linuxserver/qbittorrent:5.2.3` (`sha256:b024436f...`) | `v5.2.3` | `2.15.1` | version stable courante au 2026-07-27 (vérifiée via l'API GitHub Releases officielle, pas la mémoire) |

Digests complets et provenance dans `tests/integration/qbittorrent-matrix.toml`.
`linuxserver/qbittorrent` a été choisi car aucune image officielle
qBittorrent n'existe ; c'est l'image la plus utilisée et maintenue avec
des tags par version exacte (vérifié via l'API Docker Hub, pas supposé).
`qbit-5.2.3` a été ajoutée sans remplacer aucune des trois entrées
historiques (voir la politique de revendication en §10).

Exécuté via `make test-qbit-matrix` (les quatre) / `make test-qbit-version
QBIT_MATRIX_ID=<id>` (une seule), et par
`.github/workflows/qbittorrent-matrix.yml` (cadence détaillée ci-dessous,
jamais sur push/PR). Chaque conteneur est jetable, sur un réseau
Docker dédié. La **configuration** est hermétique
(`HOME`/`XDG_CONFIG_HOME` temporaires, `QBIT_OPS_ENV_FILE` pointant
vers un chemin garanti absent, identifiants et ports générés par
exécution, publication de port loopback uniquement) — le **réseau**,
lui, ne l'est pas : voir la réserve F-1 ci-dessous. Sa version
applicative réellement observée, sa version Web API observée, et son
architecture observée sont comparées au manifeste **avant** tout test
— un tag d'image ou une architecture qui dériveraient de l'attendu
font échouer la matrice plutôt que de continuer silencieusement
(prouvé par sabotage : version attendue
fausse, puis digest d'une autre image, tous deux rejetés avant tout
test, aucune fuite de ressource).

Preuves obtenues, désormais comblant les trois trous que les fixtures
seules ne pouvaient pas combler, pour les quatre versions :
- **login réel** : chaque scénario commence par un `auth_log_in()` contre
  le vrai conteneur ;
- **endpoint effectivement atteint** : instrumenté sans mock
  (`tests/integration/_instrumentation.py` enveloppe
  `QbittorrentSession.request` sans le remplacer) — confirme
  `torrents/pause`+`torrents/resume` sur `qbit-4.6.7` (Web API < 2.11.0)
  et `torrents/stop`+`torrents/start` sur `qbit-5.0.0`/`qbit-5.1.4`/
  `qbit-5.2.3` (Web API ≥ 2.11.0), exactement la bascule documentée en §2 ;
- **nombre d'appels réellement émis** : le `GET /app/webapiVersion`
  caché (constat P-5) est confirmé présent sur chaque mutation d'état
  torrent/tracker à plancher de version déclaré, contre les quatre
  instances réelles, pas seulement contre la bibliothèque simulée.

Aucune différence de forme de payload ni de comportement n'a été
observée entre `qbit-5.1.4` et `qbit-5.2.3` — aucune branche de
compatibilité n'a donc été ajoutée au code de production.

Contraintes respectées : `make check` reste exécutable hors ligne (la
matrice Docker n'en fait jamais partie) ; aucune mutation destructive
n'a touché autre chose que le corpus synthétique jetable à hash exact
(`tests/integration/_torrent_corpus.py`) ; aucun tracker public, DHT,
PeX ou LSD n'a été sollicité côté fonctionnel (désactivés dès le
premier démarrage via un `qBittorrent.conf` pré-scellé, voir la note
d'implémentation).

⚠️ **Réserve F-1 — isolation réseau applicative, pas réseau** : le
conteneur tourne sur un réseau Docker **dédié**, pas **interne**
(`docker network inspect` confirme `Internal=false`) ; la sortie
Internet publique depuis le conteneur a été prouvée possible
(`wget https://api.github.com` réussit). DHT/PeX/LSD/UPnP sont
désactivés côté application, et le corpus synthétique ne porte aucune
annonce vers un tracker public — mais rien n'empêche techniquement une
sortie réseau. Ajouter `--internal` a été testé et **rejeté** : cela
bloque bien l'egress mais rend aussi le port publié en loopback
injoignable, cassant le harnais entier (`qbit-ops` s'y connecte depuis
l'hôte). Ne jamais qualifier ce réseau d'« hermétique » ou
d'« isolé » — seule la **configuration** (voir §5.2 plus haut,
`HOME`/`.env`/identifiants) l'est. Une isolation réseau opposable
exigerait un relais de publication, hors périmètre de cette phase.

**Ce que cela justifie désormais (palier `TESTED` au sens de §9,
"container integration tested")** : les quatre versions exactes
ci-dessus, pour les capacités listées dans
`tests/integration/qbittorrent-matrix.toml` (`read_only`, `mutations`,
`tracker_mutations`, `capture`). **Ce que cela ne justifie pas** : une
affirmation de support pour toute version 4.6.x/5.0.x/5.1.x/5.2.x autre
que ces quatre digests précis, ni pour la ligne 4.5.x. Formulation
correcte : « container integration tested against exact versions:
4.6.7, 5.0.0, 5.1.4 and 5.2.3 » — jamais « compatible with qBittorrent
4.6–5.2 ».

### 5.3 Détection de fraîcheur (pas une mise à jour automatique)

`scripts/check_qbit_matrix_freshness.py` compare la version la plus
élevée du manifeste à la dernière release stable connue de
`qbittorrent/qBittorrent` (API GitHub Releases publique, non
authentifiée, aucun secret requis). Il ne modifie **jamais** le
manifeste et ne marque **jamais** une version plus récente comme
supportée — un résultat `STALE` signifie « revue de matrice requise »,
jamais « qbit-ops est incompatible ». Un échec réseau (`UNKNOWN`) est
explicitement distingué d'une matrice obsolète (`STALE`). Exécuté comme
job séparé (`freshness-check`) dans
`.github/workflows/qbittorrent-matrix.yml`, dont l'échec ne fait jamais
échouer le workflow ; jamais exécuté par `make check`. Testé
unitairement avec accès réseau entièrement simulé
(`tests/test_check_qbit_matrix_freshness.py`).

---

## 6. 🩺 Ce que `doctor` doit vérifier

État actuel et cible :

| Code | Aujourd'hui | Cible (phase 7) |
|---|---|---|
| `CONN003` | affiche la version applicative | inchangé |
| `CONN004` | affiche la version Web API | inchangé |
| `COMPAT001` | la chaîne de version applicative est analysable | étendu à la version **Web API** |
| `COMPAT002` | `major in {4, 5}` | remplacé par le **palier** de la version Web API |
| `COMPAT003` | — | **nouveau** : capacités disponibles vs capacités requises |
| `RUNTIME001` | `torrents/info` répond | inchangé |
| `RUNTIME002` | `transfer/info` répond | inchangé |
| `RUNTIME003` | tous les états torrent sont reconnus | inchangé |
| `RUNTIME004` | — | **nouveau** : champs obligatoires présents sur un échantillon de `torrents/info` |

Principe conservé : une version inconnue ou non analysable produit un
`warning`, **jamais** un `fail`. `qbit-ops` n'invente pas une garantie
qu'il n'a pas.

---

## 7. 🚨 Hypothèses de charge utile susceptibles de casser

Trois risques identifiés, tous testables par fixture. Détail complet dans
[l'inventaire API](audits/2026-07-qbittorrent-api-inventory.md) §3.

⚠️ **Mise à jour (2026-07-27, phase 3 du plan de refactor)** : les deux
premiers risques ci-dessous sont **corrigés**, pas seulement documentés
— voir `src/qbit_ops/qbit/fields.py` et
`docs/audits/2026-07-package-refactor-plan.md` Phase 3. Aucune version
qBittorrent n'est pour autant revendiquée testée ; ce sont des
corrections de robustesse, pas des preuves de compatibilité.

| Risque | Effet aujourd'hui | Gravité |
|---|---|---|
| `transfer/info` change de forme | ✅ **corrigé** — `qbit/fields.get_transfer_rates` lève un `TypeError` explicite pour un payload non-Mapping ; toujours un code 70 (interne), mais identifiable, plus un `AttributeError` accidentel | haute → traité |
| le champ `status` d'un tracker arrive en `IntEnum` | ✅ **corrigé** — `qbit/fields.is_disabled_tracker_status` coerce par `int()` d'abord, ce qui classe correctement un `IntEnum`-like ; un tracker désactivé ne peut plus être traité comme actif dans un plan de mutation | haute → traité |
| un nouvel état torrent apparaît | classé `unknown` → alerte `status` + `warning` `doctor` ; `pause` le traite comme actif, `resume` comme déjà actif (asymétrie non documentée) | moyenne — non traité par cette phase |

Le champ `hash` est le seul dont l'absence provoque une erreur explicite
(`RuntimeError` dans `src/qbit_ops/trackers.py`) ; `state` est obligatoire de fait
(son absence produit un rapport de santé dégradé mais valide) ; tous les
autres champs consommés dégradent silencieusement vers une valeur par
défaut.

---

## 8. 📌 Ce que `qbit-ops` peut affirmer aujourd'hui

**Peut affirmer :**

- il gère les deux vocabulaires d'état, `paused*` (4.x) et `stopped*`
  (5.x), via une règle unique et testée (`src/qbit_ops/torrent_states.py`) ;
- il mappe les sept codes de statut tracker conformément à
  `qbittorrentapi.definitions.TrackerStatus`, vérifié contre la
  bibliothèque installée ;
- les noms d'arguments de ses 13 appels correspondent aux signatures de
  `qbittorrent-api 2026.7.0`, vérifié ;
- aucune de ses commandes ordinaires n'affiche d'URL d'annonce brute,
  vérifié statiquement ;
- un `transfer_info()` non-`Mapping` échoue explicitement
  (`TypeError`), plutôt que par un `AttributeError` accidentel, vérifié
  contre un double non-Mapping construit exprès
  (`tests/test_qbit_fields.py`) ;
- un statut tracker désactivé renvoyé comme valeur `IntEnum`-like est
  correctement classé désactivé, pas actif, vérifié contre une valeur
  imitant `qbittorrentapi.definitions.TrackerStatus`'s bases réelles
  (`int, Enum`).

**Ne peut pas affirmer :**

- qu'une quelconque version de qBittorrent fonctionne — aucune n'a été
  exercée contre une instance réelle ;
- que ses budgets d'appels documentés soient exacts : chaque mutation d'état
  torrent ou de tracker *avec un plancher de version déclaré* (§3) déclenche
  un `GET /app/webapiVersion` supplémentaire, émis par la bibliothèque et
  vérifié empiriquement (`tests/test_qbit_library_http_boundary.py`,
  constat P-5) — `torrents/addTrackers` et les lectures (`torrents/info`)
  n'en émettent aucun ;
- que la branche `getattr` générale de `get_field()` (utilisée pour les
  champs torrent/tracker autres que `transfer_info`) soit exercée par
  une donnée réaliste : elle reste couverte uniquement par des objets
  factices dédiés, comme avant cette phase (constat P-1, non traité ici
  au-delà de `transfer_info`).

⚠️ **Mise à jour (2026-07-27, phase qBittorrent-boundary + fixtures)** :
le repli `torrents_start` → `torrents_resume` mentionné dans une version
antérieure de ce document a été **supprimé** (constat P-4) : dans
`qbittorrent-api 2026.7.0` les deux noms désignent la même méthode liée
(vérifié par introspection), et la branche de repli était du code mort
inatteignable. `_call_bulk_torrent_action` appelle désormais directement
`torrents_start`. De même, `auth_log_in()` est maintenant exercé par un
test dédié (constat P-7), et l'endpoint 5.x réel `torrents/stop` est
prouvé atteint par la bascule interne de `qbittorrent-api` au seuil Web
API 2.11.0, pas supposé (constat P-8) — voir
`tests/test_qbit_library_http_boundary.py`. Ces corrections/preuves
restent des faits sur le **client Python et la couche HTTP qu'il émet**,
pas des preuves d'intégration contre une instance qBittorrent réelle.

---

## 9. 📖 Terminologie des niveaux de preuve

Ce document distingue désormais explicitement les niveaux de preuve
suivants, pour éviter qu'un lecteur ne confonde « une fixture existe »
avec « une version est supportée ». Aucun de ces termes n'attribue de
**plage de version** publique — voir §1 et §4 pour les paliers de
compatibilité, qui restent inchangés par cette section.

- **`payload fixture tested`** — une fonction de la frontière de
  production (`qbit_ops.qbit.fields`, `torrent_states`, `trackers`,
  `doctor`) a été exercée contre un payload JSON (`synthetic` ou
  `official-example`, voir `tests/compatibility/README.md`) qui imite
  la **forme documentée** d'une réponse Web API. Ne prouve **rien**
  contre une instance réelle ; prouve seulement que la logique de
  lecture/classification ne lève pas et produit le résultat attendu
  pour cette forme. C'est l'état de ce dépôt au 2026-07-27 pour les
  quatre catégories de payload (torrents, trackers, transfer,
  application).
- **`container integration tested`** — une capacité de §3 a été
  exercée par un appel réel contre une instance qBittorrent réelle,
  dans un conteneur jetable sur un réseau Docker dédié (isolation
  réseau **applicative**, pas imposée par le réseau lui-même — voir la
  réserve ci-dessous), avec un résultat passant enregistré. Correspond
  au palier `TESTED` de §4 pour la
  version Web API observée. **Aucune version n'a ce statut
  aujourd'hui** — c'est l'objet de la future matrice Docker (§5.2),
  non encore commencée par cette phase.
- **`dogfooded`** — utilisé sans incident connu par l'auteur du projet
  contre son instance homelab personnelle, en usage réel et continu.
  N'est **pas** une preuve reproductible ni citable comme telle ; ne
  remplace ni `payload fixture tested` ni `container integration
  tested`. À ne mentionner, le cas échéant, qu'à titre anecdotique et
  jamais dans une table de compatibilité formelle.
- **`supported`** — réservé à une version Web API ayant atteint le
  palier `TESTED` ou `SUPPORTED_UNTESTED` de §4. Ne doit **jamais**
  être utilisé pour décrire un résultat `payload fixture tested` seul.
- **`unsupported`** — réservé à une version Web API pour laquelle une
  capacité **requise** a été observée en échec (palier `UNSUPPORTED` de
  §4). Ne doit **jamais** être déduit par absence de preuve : l'absence
  de test produit `UNTESTED_NEWER` (avertissement), pas `unsupported`.

**Application immédiate** : au 2026-07-27, la frontière qBittorrent et
l'ensemble des fixtures de compatibilité (`tests/compatibility/`) sont
`payload fixture tested`. Les quatre versions exactes de la matrice
Docker (§5.2) — `qbit-4.6.7`, `qbit-5.0.0`, `qbit-5.1.4`, `qbit-5.2.3`,
chacune identifiée par son digest d'image — sont désormais
`container integration tested` pour les capacités `read_only`,
`mutations`, `tracker_mutations` et `capture`. Aucune version n'est
`supported` au sens du palier `TESTED`/`SUPPORTED_UNTESTED` de §4 tant
que `doctor` n'a pas été étendu pour s'appuyer sur ces preuves (hors
périmètre de cette phase) ; aucune version n'est `unsupported`. Ce
document continue de ne publier aucune plage de version qBittorrent
supportée — quatre digests précis ne sont pas une plage.

---

## 10. 📏 Politique normative de revendication de compatibilité

> **Ce document, pas `AGENTS.md`, est la source normative de cette
> politique.** La revue indépendante du 2026-07-27
> (`docs/audits/2026-07-27-qbittorrent-compatibility-independent-review.md`,
> constat F-4) a relevé qu'`AGENTS.md` est listé dans `.gitignore` —
> les huit règles qui suivaient n'y existaient donc, sous forme
> traçable, pour aucun contributeur ni aucune revue. `AGENTS.md` peut
> continuer à renvoyer ici, mais ne doit plus jamais être la seule
> source.

Règles, opposables à toute documentation, sortie `doctor`, ou message
utilisateur futur :

1. `tests/integration/qbittorrent-matrix.toml` est la **source
   exécutable unique**. Aucune liste de versions dupliquée ailleurs
   n'a d'autorité.
2. Seules les versions **exactement exécutées avec succès** peuvent
   être qualifiées de `container integration tested`.
3. Une version corrective (patch) réussie n'est **jamais** une preuve
   pour toute sa ligne de version (ex. : `5.2.3` ne prouve rien sur
   `5.2.0`–`5.2.2` ni sur un futur `5.2.4`).
4. Les fixtures `synthetic`/`official-example` ne sont **jamais** une
   preuve de version — voir §9.
5. Un tag d'image mutable (`latest`, un tag flottant) n'est **jamais**
   une preuve — seul un digest observé et vérifié à l'exécution en
   est une.
6. Le digest d'image, l'architecture, la version applicative et la
   version Web API doivent tous être **observés sur le conteneur réel
   et vérifiés**, jamais seulement copiés depuis le manifeste attendu
   (voir §5.2, constat F-2/F-6).
7. `supported` est **strictement plus fort** que
   `container integration tested` et exige la politique de support
   complète de §4 — jamais utilisé pour décrire une simple exécution
   réussie.
8. Une version **non testée** n'est **jamais** déclarée incompatible au
   seul motif de son absence du manifeste ; « plus récente que les
   preuves actuelles » est une information, pas un défaut (voir §19 —
   note : la numérotation `doctor` de la revue indépendante référence
   une future phase, non traitée ici).
9. La version stable courante de qBittorrent doit être évaluée dans la
   matrice **avant** toute publication publique de `qbit-ops`.

Formulation la plus forte actuellement soutenue par les preuves :

> Container integration tested against the exact qBittorrent versions
> 4.6.7, 5.0.0, 5.1.4 and 5.2.3 on amd64.

Formulations **interdites**, quel que soit le contexte (documentation,
`README`, sortie `doctor`, message de commit) :

- « compatible with qBittorrent 4.6–5.2 »
- « supports qBittorrent 4.x and 5.x »
- « all qBittorrent 5.x versions are supported »
