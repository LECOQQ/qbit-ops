# Changelog

All notable changes will be documented here, using the [Keep A Changelog](https://keepachangelog.com/en/1.0.0/) formalism,
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)

## [0.4.0](https://github.com/LECOQQ/qbit-ops/compare/v0.3.0...v0.4.0) (2026-08-13)


### Features

* **backup:** add additive category/tags/tracker restore from export ([ede907c](https://github.com/LECOQQ/qbit-ops/commit/ede907cba372e3e5cc4db870180ee91c416bfded))
* **dist:** publish qbit-ops to PyPI via Trusted Publishing ([70c48b1](https://github.com/LECOQQ/qbit-ops/commit/70c48b130d28eaffe3f9ea40bb027a41295885ac))
* **qbit-core:** add pure parsers for operator-typed quantities ([5aacbc1](https://github.com/LECOQQ/qbit-ops/commit/5aacbc1e713a168ed769c1ff58d27a22618f9b11))
* **qbit-core:** add the composable selection model and its validation ([979b871](https://github.com/LECOQQ/qbit-ops/commit/979b87108749f16323170c32096e1d1a737fd031))
* **qbit-core:** evaluate every cheap selection predicate ([9e5cfcc](https://github.com/LECOQQ/qbit-ops/commit/9e5cfcc1e4f002428b46e712248c4b8146917b66))
* **qbit-core:** report field absence explicitly for bounded predicates ([9a6c5ab](https://github.com/LECOQQ/qbit-ops/commit/9a6c5ab663877913f389c8f589f05653b5c309fe))
* **selection:** close the last filter gaps and drop the TUI refresh debt ([5a49bda](https://github.com/LECOQQ/qbit-ops/commit/5a49bdafdfcf5679d82173c9642eb73bcaadb3ba))
* **selection:** filter by completion date, on the observed -1 sentinel ([42f5411](https://github.com/LECOQQ/qbit-ops/commit/42f5411a0917afc0c3b0348108c4c7092a9f4cf8))
* **selection:** filter by inactivity, on observed last_activity semantics ([8f4a47b](https://github.com/LECOQQ/qbit-ops/commit/8f4a47b9aae619a3d216b95b56df18bac8fba18f))
* **tooling:** add worktree and documentation-consistency make targets ([00b7e9d](https://github.com/LECOQQ/qbit-ops/commit/00b7e9d87c4487b5dc213f0d0a973a052d7d042e))
* **torrents:** accept the composable filters on every bulk mutation ([69b5e1f](https://github.com/LECOQQ/qbit-ops/commit/69b5e1f88effa3f4eaa32237c6ea94f4e6cd4bd8))
* **torrents:** add .torrent import (file/dir/zip) via WebUI API ([cb1f01a](https://github.com/LECOQQ/qbit-ops/commit/cb1f01a8025ce277d8f4a2b82f91cc0e6359d945))
* **torrents:** add HIGH-risk destructive `torrents delete` ([24eb539](https://github.com/LECOQQ/qbit-ops/commit/24eb539a278255ea2e9bd25f240836275decd343))
* **torrents:** expose the composable cheap filters on `torrents list` ([0d56958](https://github.com/LECOQQ/qbit-ops/commit/0d56958a80da7dbfd2bf0fd4c9f7090b9c38119f))
* **torrents:** filter by tracker presence and excluded tracker hosts ([ffc3ecf](https://github.com/LECOQQ/qbit-ops/commit/ffc3ecfa29b9326e8ff88b61cb3ce49a6cba65be))
* **torrents:** serialize the composable filters and repeat --tracker ([cbcab49](https://github.com/LECOQQ/qbit-ops/commit/cbcab49186e89c7ad4a00e50637d56876ec8bb2a))
* **trackers:** scope add-if-present with torrent filters ([e294f5d](https://github.com/LECOQQ/qbit-ops/commit/e294f5d31fb52a6c8f126ad1af3738c9c4659bf4))
* **trackers:** scope every tracker operation with the composable filters ([c1c4f11](https://github.com/LECOQQ/qbit-ops/commit/c1c4f110926e00e5b6863fee38056f23edd10c68))
* **tui:** add instance-wide lifetime stats to the Overview panel ([8755339](https://github.com/LECOQQ/qbit-ops/commit/8755339e9ae7e71230fdf89ddb00ee036d5ae539))
* **version:** report qbit-ops, Python and qBittorrent versions ([4a53ea5](https://github.com/LECOQQ/qbit-ops/commit/4a53ea536f6cb1e0518d593f6d00b994796b87b2))


### Bug Fixes

* **qbit-core:** case-insensitive dedup and blank-hash validation in hash-based ops ([5bb3840](https://github.com/LECOQQ/qbit-ops/commit/5bb3840fca3ae1a02731dcd89e12f41dc8b60bed))
* **selection:** make tracker filters crash-proof and selection uniform ([da8f53b](https://github.com/LECOQQ/qbit-ops/commit/da8f53b29d898dca115c8e6139dc96fa7abb4339))
* **test:** run matrix containers as the host user, not a hardcoded uid ([d3fb98d](https://github.com/LECOQQ/qbit-ops/commit/d3fb98d356edff08de3620618e367c7b81bbf285))
* **trackers:** stop counting DHT/PeX/LSD as trackers ([af28e0e](https://github.com/LECOQQ/qbit-ops/commit/af28e0ec2b26cdcda7a79685994b5961fc78b278))
* **version:** keep --version silent during shell completion ([981785f](https://github.com/LECOQQ/qbit-ops/commit/981785fd1e9d171553778966258e0fee0d235f50))


### Performance Improvements

* **tests:** run the hermetic suites under pytest-xdist ([689205a](https://github.com/LECOQQ/qbit-ops/commit/689205abba0d97d952f01ade6ff601e858338e10))


### Documentation

* add philosophy ([bc1f695](https://github.com/LECOQQ/qbit-ops/commit/bc1f6957ff61bc036a31c8647a70dfbadd27ba37))
* add public product roadmap ([2cbdd5a](https://github.com/LECOQQ/qbit-ops/commit/2cbdd5ac29361d122eb84d4aab372a58363663f0))
* **readme:** add a *how is qbit-ops different* section ([79e4f02](https://github.com/LECOQQ/qbit-ops/commit/79e4f02eed13830e8d6d0a157cc76810604c5818))
* **readme:** add link to roadmap ([80a2784](https://github.com/LECOQQ/qbit-ops/commit/80a27849ce8499b2b909aa41a6598cbdcdef917f))
* **readme:** update assets, add qbittorrent-cli ([7aa1067](https://github.com/LECOQQ/qbit-ops/commit/7aa10675441f9ae4387786296f702931323ed646))

## [0.3.0](https://github.com/LECOQQ/qbit-ops/compare/v0.2.0...v0.3.0) (2026-08-02)


### Features

* **cli:** add progress bars for bulk actions and styled errors ([81d8fb0](https://github.com/LECOQQ/qbit-ops/commit/81d8fb0ce8e415ca9f929787898e0471f7b4bc16))
* **cli:** add Rich output and shell completion ([0a416a6](https://github.com/LECOQQ/qbit-ops/commit/0a416a60d1d7f16b6b3d3d812767e82a045318d5))
* **cli:** add transient interactive progress ([b70c31f](https://github.com/LECOQQ/qbit-ops/commit/b70c31f29b74538dd35bbb7d70d626e6fbeebd67))
* **cli:** unify read-only output formats ([3654d27](https://github.com/LECOQQ/qbit-ops/commit/3654d27c00ca9112d0cc148eaba2500eefcc1596))
* **doctor:** add structured diagnostic command ([4382ca0](https://github.com/LECOQQ/qbit-ops/commit/4382ca0728166ee63fdc6bd9bbd4bd23e9799166))
* **doctor:** report exact compatibility evidence ([93c60de](https://github.com/LECOQQ/qbit-ops/commit/93c60de40a4ecda4079ec3b363a1ae37c9b166f2))
* **execution:** add risk-based mutation policies ([b995e4c](https://github.com/LECOQQ/qbit-ops/commit/b995e4cdb622b81e7b726170368b07fa61b51957))
* **explain:** add evidence-based diagnostics ([3e5e749](https://github.com/LECOQQ/qbit-ops/commit/3e5e74963aac2bb968240e191c7ee51e77f41a72))
* **filters:** unify torrent selection ([7eb8cd3](https://github.com/LECOQQ/qbit-ops/commit/7eb8cd32bd802ca96fee601592f4534dfc2a93c5))
* **status:** add live watch mode ([5aa4ee1](https://github.com/LECOQQ/qbit-ops/commit/5aa4ee143e4fb7839bc344218b7b68d02f850804))
* **status:** add operational snapshot command ([a1aba02](https://github.com/LECOQQ/qbit-ops/commit/a1aba02fe4b1b08cd0e77c6b4674bbc68184b764))
* **torrents:** add hash-centric selection ([679759f](https://github.com/LECOQQ/qbit-ops/commit/679759fc67ec6715a781f1d3adde0d14e3a71391))
* **trackers:** add bulk passkey replacement command ([5080e93](https://github.com/LECOQQ/qbit-ops/commit/5080e9378a3c856182e3196d61ccbe36347a655f))
* **trackers:** add bulk passkey replacement command ([73c788a](https://github.com/LECOQQ/qbit-ops/commit/73c788a8984300bc699b3fd9d30b6787cbf71897))
* **trackers:** add structured status reporting ([e865f01](https://github.com/LECOQQ/qbit-ops/commit/e865f01fff8f1ff4b3252d118d7221d8c86de421))
* **tui:** add low-risk bulk actions ([6f23749](https://github.com/LECOQQ/qbit-ops/commit/6f237493f45c703991cecd7fd35faa3c1c8ac2b1))
* **tui:** add overview-first workspaces ([5a8cba2](https://github.com/LECOQQ/qbit-ops/commit/5a8cba22c1ed9bfcc6e94230261e05d52bfb7ff1))
* **tui:** add read-only torrent dashboard ([0bb46d3](https://github.com/LECOQQ/qbit-ops/commit/0bb46d3e211e2a9caf4b20c15d2859aa5faf1267))
* **tui:** complete read-only operator dashboard ([0a5d327](https://github.com/LECOQQ/qbit-ops/commit/0a5d32786dcfbeeec53a8bd8af7b62bae6d08635))
* **tui:** finalize torrent workspace and visual identity ([a754165](https://github.com/LECOQQ/qbit-ops/commit/a754165013a8ed67aab516226dd7497ec4f5cbc0))


### Bug Fixes

* **cli:** distinguish validation and runtime errors ([41831a4](https://github.com/LECOQQ/qbit-ops/commit/41831a48c25610fb1b61d63270c7ef67b6385fb5))
* **doctor:** remove overbroad qBittorrent support claim ([5d85401](https://github.com/LECOQQ/qbit-ops/commit/5d85401023c80a16aa2c719a4985b69a03ef10c3))
* **execution:** report truthful mutation outcomes ([8500f2e](https://github.com/LECOQQ/qbit-ops/commit/8500f2efe353bdc065a8fa4b49eaa926cbbd26c9))
* **package:** resolve version from installed metadata ([d983e05](https://github.com/LECOQQ/qbit-ops/commit/d983e0571d14444074285ae693ff3e76ac437bc7))
* **release:** restore version checker type safety ([64903c2](https://github.com/LECOQQ/qbit-ops/commit/64903c2150597d7448b706d001b79a1ab9d08f92))
* **security:** redact tracker secrets from all outputs ([b6901bd](https://github.com/LECOQQ/qbit-ops/commit/b6901bd23aefd687d5570c82f5d8cddd6325f503))
* **tests:** disabled forced rich color in CI ([6db95dc](https://github.com/LECOQQ/qbit-ops/commit/6db95dc0bf94250bcb49bafa7a86aa37cbe46efd))
* **tests:** fix cross-command, fix compatibility, doc-consistency ([5bd6d0a](https://github.com/LECOQQ/qbit-ops/commit/5bd6d0adb212ba0a21cf011303b12b8f2957e2c2))
* **tui:** close remaining mutation lifecycle gaps ([f091c23](https://github.com/LECOQQ/qbit-ops/commit/f091c233db74ed51436933639d81b469616c31e7))
* **tui:** fixed search bar ([559f592](https://github.com/LECOQQ/qbit-ops/commit/559f59200f521f4a34a00ff43b13a7c69aaa3277))
* **tui:** harden bulk mutation lifecycle ([2ddc5c3](https://github.com/LECOQQ/qbit-ops/commit/2ddc5c3ef8d38e0764d7271039dedda6814a6a4f))
* **tui:** keep remote work off the event loop ([d7c5e4a](https://github.com/LECOQQ/qbit-ops/commit/d7c5e4ab75414ff6754ea1e42e2bd05ac4284ded))
* **tui:** polish contextual controls and rendering ([160026c](https://github.com/LECOQQ/qbit-ops/commit/160026c11ac7964103a5b61792ca562e82888607))
* **tui:** stabilize table events and controls ([6342cf9](https://github.com/LECOQQ/qbit-ops/commit/6342cf93f747b3f2fbee2f82180fc014b5af0c97))
* **version:** fixed version badge & sync venv ([36ecbb1](https://github.com/LECOQQ/qbit-ops/commit/36ecbb137160f04e85a98cc2a313824f6227e74c))
* **vhs:** update timelines ([b4fb35e](https://github.com/LECOQQ/qbit-ops/commit/b4fb35e4762eb33475547704f48ca0c8b6008a9c))


### Performance Improvements

* **tui:** reduce torrent workspace latency ([d522329](https://github.com/LECOQQ/qbit-ops/commit/d522329c188229ed46c50f94be99a914dc9a32c0))


### Documentation

* simplify README and doc set ([e2ae6e2](https://github.com/LECOQQ/qbit-ops/commit/e2ae6e2c9373b8b658b85b11d30cb0b151dea870))
* **testing:** add tracked testing tier policy ([d82c9bc](https://github.com/LECOQQ/qbit-ops/commit/d82c9bc6eb5335983ccdd146f86ca646e791c5f4))

## [0.2.0](https://github.com/LECOQQ/qbit-ops/compare/v0.1.0...v0.2.0) (2026-06-14)


### Features

* **backup:** add export diff command ([ac754d3](https://github.com/LECOQQ/qbit-ops/commit/ac754d397b8dee7cce6af7c8471a83631b5afdf1))
* **backup:** add full export and harmonize audit JSON output ([dcc2aa5](https://github.com/LECOQQ/qbit-ops/commit/dcc2aa5d789347ee920c647e764eeeaa8f32d88e))
* **cli:** add config doctor and torrent listing ([90ec246](https://github.com/LECOQQ/qbit-ops/commit/90ec2468731b3f38a7e44b820f82cc8fea50646c))
* **torrents:** add bulk pause, resume and reannounce ([7fa2c0f](https://github.com/LECOQQ/qbit-ops/commit/7fa2c0f3e476850836ddf73fb40b03257cf153ba))
* **torrents:** add bulk start ([74cce6c](https://github.com/LECOQQ/qbit-ops/commit/74cce6cc0e1febb740dddc4236cd5b286c5160d7))
* **torrents:** add category listing and filtering ([6e8484b](https://github.com/LECOQQ/qbit-ops/commit/6e8484b70b7cfc2894d8b399cf406ee2f57e0a8e))
* **torrents:** add inspect command ([f7c7b27](https://github.com/LECOQQ/qbit-ops/commit/f7c7b27817aa505f1e1330e73261d36e2fe4fce1))
* **torrents:** add name search to inspect command ([ed8e6a2](https://github.com/LECOQQ/qbit-ops/commit/ed8e6a2dc77929ce5547846bae33f44d7f5cf5c5))
* **torrents:** add tracker filter to list command ([112fecd](https://github.com/LECOQQ/qbit-ops/commit/112fecd18383f2c37562ceccb7c213be321f11b3))
* **torrents:** added --all for bulk operations ([b7066b4](https://github.com/LECOQQ/qbit-ops/commit/b7066b4846c42ebf20fb9fe4100af77d6b7fb4c1))
* **trackers:** add bulk tracker replacement ([2a6da4d](https://github.com/LECOQQ/qbit-ops/commit/2a6da4d3e542454797fbf60c666a17726402ae3c))
* **trackers:** add health audit command ([369bb56](https://github.com/LECOQQ/qbit-ops/commit/369bb565b226e3ab7c908a0cc1770f64e8ad9ba2))
* **trackers:** add inspect, export and verbose output ([6c8513a](https://github.com/LECOQQ/qbit-ops/commit/6c8513a0cc6fc8e29e4c8f2db2d21afabf272a55))
* **trackers:** added exit codes ([e7664e2](https://github.com/LECOQQ/qbit-ops/commit/e7664e234ade0ea2e32309c49a0b1681168523f9))


### Documentation

* updated badges and removed version section ([d57d935](https://github.com/LECOQQ/qbit-ops/commit/d57d9358c2da7e57fd985368ee24a675a81323cc))

## 0.1.0 (2026-06-13)


### Features

* **ci:** added simple ci (check+tests) ([5bfc03a](https://github.com/LECOQQ/qbit-ops/commit/5bfc03a7677084460b18aec3ac0ae3ceda1bd95d))
* **pipx:** added pipx installation instructions ([38137d1](https://github.com/LECOQQ/qbit-ops/commit/38137d1c867046a78f42a3e45b88fe124752a351))


### Bug Fixes

* **ci:** fixed make recipe for ci ([2061e1a](https://github.com/LECOQQ/qbit-ops/commit/2061e1a4a9aa43517e02de43f859f9ed02a45951))


### Documentation

* added a changelog ([7431d0d](https://github.com/LECOQQ/qbit-ops/commit/7431d0d0171a37ca889b9cf44669b6ce3f2c1594))
* **readme:** added status badges ([5d56943](https://github.com/LECOQQ/qbit-ops/commit/5d569436e7bddfbc4915f8273b548382f19535d4))
* **version:** removed version file ([32a7848](https://github.com/LECOQQ/qbit-ops/commit/32a7848ed365720208365d26fc2e71633d436998))
