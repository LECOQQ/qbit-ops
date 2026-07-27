---
title: "Compatibilité qBittorrent"
description: "Ce que qbit-ops sait faire, sur quelles versions, et ce qui est réellement prouvé — politique fondée sur la Web API"
status: draft
---

# 🔗 Compatibilité qBittorrent

> **Statut : `draft`.** Ce document définit la **politique** de
> compatibilité et l'état des preuves au 2026-07. Il ne fixe
> volontairement **aucune borne de version définitive** : à ce jour,
> aucune version de qBittorrent n'est vérifiée par exécution. Les bornes
> seront inscrites ici au fur et à mesure que les phases 5 et 6 du
> [plan de refactor](audits/2026-07-package-refactor-plan.md) produiront
> des preuves.

---

## 1. ⚠️ État des preuves — à lire en premier

| Question | Réponse au 2026-07 |
|---|---|
| Une version de qBittorrent est-elle testée par `qbit-ops` ? | **Non, aucune.** |
| Y a-t-il un test contre une instance réelle ? | **Non.** |
| Y a-t-il des fixtures issues de réponses authentiques ? | **Non.** |
| La CI exerce-t-elle qBittorrent ? | **Non** — `make ci` = lint, Pyright, pytest, `--help`. |
| Que valent les tests existants ? | Ils prouvent la **logique** de `qbit-ops` (filtres, plans, sûreté, non-fuite de secrets), contre un double écrit à la main. Ils ne prouvent **rien** sur qBittorrent. |
| `auth_log_in()` est-il testé ? | **Non** — le harnais de test remplace la fabrique de client en entier. |

La mention actuelle *« qbit-ops has been validated against qBittorrent
4.x and 5.x »* (`src/qbit_ops/doctor.py`, `docs/COMMANDS.md`) est une **assertion
déclarative sans vérification**. Elle est conservée telle quelle dans le
code tant que la phase 4 n'a pas remplacé le modèle, mais elle ne doit
pas être lue comme une garantie.

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

### 5.2 Par intégration réelle (phase 6 — job CI séparé, Docker)

| Version qBittorrent | Motif |
|---|---|
| dernière 4.6.x | dernier 4.x, endpoints `pause`/`resume` |
| première 5.0.x | bascule `stop`/`start` |
| dernière 5.x | état de l'art |

Ce sont les **seules** exécutions capables d'attribuer un palier `TESTED`.
Elles comblent trois trous que les fixtures ne peuvent pas combler : le
login réel, l'endpoint effectivement atteint, et le nombre d'appels
réellement émis.

Contraintes : `make check` doit rester exécutable hors ligne ; aucune
mutation destructive (pause/start/reannounce et ajout/retrait de tracker
sur un torrent jetable uniquement).

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
  dans un conteneur Docker jetable et hermétique, avec un résultat
  passant enregistré. Correspond au palier `TESTED` de §4 pour la
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
les fixtures de compatibilité (`tests/compatibility/`) sont
`payload fixture tested`. Aucune version, aucune capacité, n'est
`container integration tested`, `supported` ou `unsupported` au sens
de cette section — et ce document continue de ne publier aucune plage
de version qBittorrent supportée.
