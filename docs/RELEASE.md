---
title: "Releases et versioning"
description: "Comment qbit-ops calcule, propose et publie une version, et comment vérifier la cohérence entre les fichiers concernés"
status: stable
---

# 🚀 Releases et versioning

Ce document décrit le contrat de version de `qbit-ops` : d'où vient la
version, qui la met à jour, et comment vérifier qu'elle reste cohérente
partout où elle est déclarée.

## 🔗 Le contrat de version

    pyproject.toml               → version déclarée en contrôle de source
    Release Please               → propose et met à jour la prochaine version
    Poetry build/install         → écrit les métadonnées de distribution
    importlib.metadata.version() → version runtime (qbit_ops.__version__)
    dépôt brut, non installé     → repli explicite "0+unknown"

`qbit_ops.__version__` (`src/qbit_ops/__init__.py`) ne lit jamais
`pyproject.toml` directement : il lit uniquement
`importlib.metadata.version("qbit-ops")`, ce qui fonctionne identiquement
pour un wheel, une installation pipx, ou une installation éditable, et ne
dépend jamais de la profondeur du paquet sous la racine du dépôt. Le seul
cas de repli est une distribution absente (dépôt cloné sans installation),
qui retourne le marqueur explicite `0+unknown` — jamais une chaîne qui
ressemble à une vraie version. Voir `tests/test_version_resolution.py`.

## 📦 Le contrôleur de release

Le seul contrôleur de release est le workflow GitHub Actions
`.github/workflows/release-please.yml`, qui appelle
`googleapis/release-please-action@v4` sur chaque push vers `main`. Il n'y a
pas de GitHub App Release Please installée en parallèle : les deux
contrôleurs ne doivent jamais coexister (un même dépôt ne doit avoir
qu'un seul contrôleur, sous peine de PR de release dupliquées ou
divergentes).

Permissions du workflow : `contents: write` et `pull-requests: write`
uniquement, avec le `GITHUB_TOKEN` par défaut — pas de PAT, pas
`id-token: write`, pas de permission liée à un registre de paquets.

**Limite connue du `GITHUB_TOKEN` par défaut** : un commit poussé par
`github-actions[bot]` (via ce token) ne redéclenche pas les workflows
`on: push`/`on: pull_request` d'un autre workflow. En pratique, `ci.yml`
(et donc `make check`, `check-version` inclus) ne s'exécute **pas** sur la
PR de release elle-même. Ce n'est pas un bug à corriger silencieusement :
introduire un PAT dédié uniquement pour lever cette limite serait hors du
périmètre de cette phase. Avant de merger une PR de release, relancer les
vérifications manuellement si un doute existe (`make check`).

## 🔢 Configuration Release Please

`release-please-config.json` (mode manifest, paquet racine `.`) :

- `release-type: "python"` — l'updateur natif Python.
- `changelog-path: "CHANGELOG.md"`.
- `include-v-in-tag: true`, `include-component-in-tag: false` — tags
  `vX.Y.Z`, jamais de préfixe de composant.
- `bump-minor-pre-major: true` — tant que la version est `< 1.0.0`, un
  commit `feat!:` (breaking change) ne bump que le **mineur**, pas le
  majeur. Voir `docs/DECISIONS.md` (2026-07-27) pour pourquoi ce réglage
  a dû être rendu explicite.
- `extra-files` : un updateur TOML générique sur
  `$.tool.commitizen.version`, pour garder `[tool.commitizen].version`
  synchronisé avec `[tool.poetry].version` (décision du 2026-06-14).

**Updateur natif vs. updateur TOML explicite** : la stratégie Python native
de Release Please met déjà à jour `[tool.poetry].version` sans
configuration supplémentaire — vérifié directement contre le paquet
`release-please@17.10.4` (`updaters/python/pyproject-toml.js`) et contre le
contenu réel de `pyproject.toml`. Aucun `extra-files` n'est nécessaire pour
la clé canonique. L'unique `extra-files` du dépôt cible une clé
**différente** (`tool.commitizen.version`), qui n'est pas la déclaration
canonique mais un second champ de version pré-existant à garder aligné.

La stratégie Python native met aussi en file une mise à jour de
`src/qbit_ops/__init__.py` (recherche d'un motif
`__version__ = "X.Y.Z"`) : ce motif ne correspond jamais au contenu réel du
fichier (`__version__ = _resolve_version()`), donc cette mise à jour est un
no-op vérifié — aucun littéral de version n'est jamais écrit dans ce
fichier par Release Please.

## 🔁 Cycle de vie d'une PR de release

    Conventional Commits (feat:, fix:, feat!:, ...)
    → Release Please ouvre/actualise une PR de release
    → la PR contient : pyproject.toml + manifest + CHANGELOG.md
    → relecture humaine, puis merge de la PR de release
    → Release Please crée le tag vX.Y.Z et la GitHub Release
    → les installs futures lisent la version via importlib.metadata

Ne jamais fusionner une PR de fonctionnalité ordinaire ne crée **pas** de
release à elle seule : une release n'existe qu'après le merge de la PR de
release dédiée que Release Please maintient à jour à chaque push sur
`main`.

**Demander une version précise** : ne pas éditer `pyproject.toml` à la
main. Utiliser un commit `Release-As: X.Y.Z` dans le message du prochain
commit conventionnel poussé sur `main` — Release Please recalculera la PR
de release en conséquence.

**Comportement attendu des commits** (pré-1.0, `bump-minor-pre-major` actif) :

| Type de commit | Effet sur la version |
| --- | --- |
| `fix:` | patch |
| `feat:` | mineur |
| `feat!:` / `BREAKING CHANGE:` | mineur (au lieu de majeur, tant que `< 1.0.0`) |

## ✅ Vérifier la cohérence des versions

    make check-version
    python3 scripts/check_version_sync.py
    python3 scripts/check_version_sync.py --tag v0.3.0

`scripts/check_version_sync.py` (bibliothèque standard uniquement, aucun
accès réseau, n'utilise jamais `importlib.metadata`) vérifie que :

1. une version canonique existe dans `pyproject.toml`
   (`[project].version` ou `[tool.poetry].version`) ;
2. `.release-please-manifest.json["."]` existe ;
3. la version déclarée correspond à la version du manifest ;
4. `[project].version` et `[tool.poetry].version` ne peuvent pas diverger
   silencieusement s'ils coexistent tous les deux ;
5. `[tool.commitizen].version`, s'il existe, ne diverge pas de la version
   canonique (ce qui détecterait un `extra-files` cassé ou périmé) ;
6. un tag Git optionnel (`--tag vX.Y.Z`) correspond à la version déclarée,
   au format `vX.Y.Z` strict.

`make check-version` fait partie de `make check` (donc de `make ci`) sans
condition : la PR de fonctionnalité courante (`0.2.0`/`0.2.0`) et l'état
après une PR de release (`0.3.0`/`0.3.0`) sont tous deux cohérents, il n'y
a donc pas de blocage d'amorçage à contourner.

## 🚫 Hors périmètre

La publication PyPI/TestPyPI n'est délibérément pas configurée : ni
permission `id-token: write`, ni étape de build/upload de paquet, ni action
de publication ne sont présentes dans ce dépôt. Ce document ne couvre que
le versioning et les GitHub Releases.
