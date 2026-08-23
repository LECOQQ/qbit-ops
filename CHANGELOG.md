# Changelog

All notable changes will be documented here, using the [Keep A Changelog](https://keepachangelog.com/en/1.0.0/) formalism,
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)

## [0.5.0](https://github.com/LECOQQ/qbit-ops/compare/v0.4.0...v0.5.0) (2026-08-23)


### ⚠ BREAKING CHANGES

* **search:** `torrents inspect --name` and `--limit` are removed with no alias -- `torrents search` replaces them. In search output, `match_score` becomes `match`, the tier name; `summary.matched` now counts before truncation, alongside `returned` and `truncated`, so it answers "how many are there" rather than "how many did you print".

### Features

* **backup:** add additive category/tags/tracker restore from export ([ede907c](https://github.com/LECOQQ/qbit-ops/commit/ede907cba372e3e5cc4db870180ee91c416bfded))
* **backup:** add export diff command ([ac754d3](https://github.com/LECOQQ/qbit-ops/commit/ac754d397b8dee7cce6af7c8471a83631b5afdf1))
* **backup:** add full export and harmonize audit JSON output ([dcc2aa5](https://github.com/LECOQQ/qbit-ops/commit/dcc2aa5d789347ee920c647e764eeeaa8f32d88e))
* **ci:** added simple ci (check+tests) ([5bfc03a](https://github.com/LECOQQ/qbit-ops/commit/5bfc03a7677084460b18aec3ac0ae3ceda1bd95d))
* **cli:** add config doctor and torrent listing ([90ec246](https://github.com/LECOQQ/qbit-ops/commit/90ec2468731b3f38a7e44b820f82cc8fea50646c))
* **cli:** add progress bars for bulk actions and styled errors ([81d8fb0](https://github.com/LECOQQ/qbit-ops/commit/81d8fb0ce8e415ca9f929787898e0471f7b4bc16))
* **cli:** add Rich output and shell completion ([0a416a6](https://github.com/LECOQQ/qbit-ops/commit/0a416a60d1d7f16b6b3d3d812767e82a045318d5))
* **cli:** add transient interactive progress ([b70c31f](https://github.com/LECOQQ/qbit-ops/commit/b70c31f29b74538dd35bbb7d70d626e6fbeebd67))
* **cli:** unify read-only output formats ([3654d27](https://github.com/LECOQQ/qbit-ops/commit/3654d27c00ca9112d0cc148eaba2500eefcc1596))
* **config:** guide a fresh install to its first working command ([7477896](https://github.com/LECOQQ/qbit-ops/commit/747789602b10f0c7e88ebc19c30ba113b8925242))
* **dist:** publish qbit-ops to PyPI via Trusted Publishing ([70c48b1](https://github.com/LECOQQ/qbit-ops/commit/70c48b130d28eaffe3f9ea40bb027a41295885ac))
* **docker:** publish multi-arch container images to GHCR ([9c129e0](https://github.com/LECOQQ/qbit-ops/commit/9c129e0d9f0bcac4f75236e0e626bb6ebd13908d))
* **doctor:** add structured diagnostic command ([4382ca0](https://github.com/LECOQQ/qbit-ops/commit/4382ca0728166ee63fdc6bd9bbd4bd23e9799166))
* **doctor:** report exact compatibility evidence ([93c60de](https://github.com/LECOQQ/qbit-ops/commit/93c60de40a4ecda4079ec3b363a1ae37c9b166f2))
* **execution:** add risk-based mutation policies ([b995e4c](https://github.com/LECOQQ/qbit-ops/commit/b995e4cdb622b81e7b726170368b07fa61b51957))
* **explain:** add evidence-based diagnostics ([3e5e749](https://github.com/LECOQQ/qbit-ops/commit/3e5e74963aac2bb968240e191c7ee51e77f41a72))
* **filters:** unify torrent selection ([7eb8cd3](https://github.com/LECOQQ/qbit-ops/commit/7eb8cd32bd802ca96fee601592f4534dfc2a93c5))
* **mcp:** add an aggregate tool over a filtered selection ([a5176dc](https://github.com/LECOQQ/qbit-ops/commit/a5176dc11c40fbabc12c8185f797a21b36b2a331))
* **mcp:** add an experimental read-only MCP surface ([f5e561b](https://github.com/LECOQQ/qbit-ops/commit/f5e561b8f6798263890d4c469121ae4912611f12))
* **pipx:** added pipx installation instructions ([38137d1](https://github.com/LECOQQ/qbit-ops/commit/38137d1c867046a78f42a3e45b88fe124752a351))
* **qbit-core:** add pure parsers for operator-typed quantities ([5aacbc1](https://github.com/LECOQQ/qbit-ops/commit/5aacbc1e713a168ed769c1ff58d27a22618f9b11))
* **qbit-core:** add the composable selection model and its validation ([979b871](https://github.com/LECOQQ/qbit-ops/commit/979b87108749f16323170c32096e1d1a737fd031))
* **qbit-core:** evaluate every cheap selection predicate ([9e5cfcc](https://github.com/LECOQQ/qbit-ops/commit/9e5cfcc1e4f002428b46e712248c4b8146917b66))
* **qbit-core:** report field absence explicitly for bounded predicates ([9a6c5ab](https://github.com/LECOQQ/qbit-ops/commit/9a6c5ab663877913f389c8f589f05653b5c309fe))
* **search:** add `torrents search` and make the TUI `/` tolerant ([f5ef07f](https://github.com/LECOQQ/qbit-ops/commit/f5ef07fe89948b42d9bbd95dad284385f7d1d28e))
* **search:** add the ranking engine behind `torrents search` ([5b1fc28](https://github.com/LECOQQ/qbit-ops/commit/5b1fc283d66c31dc3645b4be989d17a09ff6c42f))
* **selection:** close the last filter gaps and drop the TUI refresh debt ([5a49bda](https://github.com/LECOQQ/qbit-ops/commit/5a49bdafdfcf5679d82173c9642eb73bcaadb3ba))
* **selection:** filter by completion date, on the observed -1 sentinel ([42f5411](https://github.com/LECOQQ/qbit-ops/commit/42f5411a0917afc0c3b0348108c4c7092a9f4cf8))
* **selection:** filter by inactivity, on observed last_activity semantics ([8f4a47b](https://github.com/LECOQQ/qbit-ops/commit/8f4a47b9aae619a3d216b95b56df18bac8fba18f))
* **stats:** read a cumulative seed time in conventional units ([b32936b](https://github.com/LECOQQ/qbit-ops/commit/b32936bdcfeabb44f86f099e72e4fdda042ed8cb))
* **status,explain:** stop flagging a stall completed from local data ([98fce0a](https://github.com/LECOQQ/qbit-ops/commit/98fce0af11018216e5cd95d1fb8f04eb21ab935f))
* **status:** add live watch mode ([5aa4ee1](https://github.com/LECOQQ/qbit-ops/commit/5aa4ee143e4fb7839bc344218b7b68d02f850804))
* **status:** add operational snapshot command ([a1aba02](https://github.com/LECOQQ/qbit-ops/commit/a1aba02fe4b1b08cd0e77c6b4674bbc68184b764))
* **tooling:** add worktree and documentation-consistency make targets ([00b7e9d](https://github.com/LECOQQ/qbit-ops/commit/00b7e9d87c4487b5dc213f0d0a973a052d7d042e))
* **tooling:** check references inside code prose, not only Markdown ([e7bc498](https://github.com/LECOQQ/qbit-ops/commit/e7bc498d57f488fb98c03a95dd7f666272fefbdc))
* **tooling:** enforce commit provenance and prove squash integration ([33f54ef](https://github.com/LECOQQ/qbit-ops/commit/33f54ef4a092094318713b49530bb89eee16d544))
* **tooling:** gate AI hygiene and break the diff down by kind of work ([7fa358f](https://github.com/LECOQQ/qbit-ops/commit/7fa358feb8e3396680a3e80a7267c8297e881ec6))
* **tooling:** give each feature worktree its own proven virtualenv ([7208082](https://github.com/LECOQQ/qbit-ops/commit/7208082447c800b0afa9603a6ed67db1f2ebf0be))
* **tooling:** isolate the test suite from the operator's own instance ([4d84361](https://github.com/LECOQQ/qbit-ops/commit/4d843611d267561e7c957a311e5acbd67b5b09ea))
* **tooling:** refuse a commit body written as prose ([f988518](https://github.com/LECOQQ/qbit-ops/commit/f9885180ac27ccd5b701b64023f8d111f4168f61))
* **torrents:** accept the composable filters on every bulk mutation ([69b5e1f](https://github.com/LECOQQ/qbit-ops/commit/69b5e1f88effa3f4eaa32237c6ea94f4e6cd4bd8))
* **torrents:** add .torrent import (file/dir/zip) via WebUI API ([cb1f01a](https://github.com/LECOQQ/qbit-ops/commit/cb1f01a8025ce277d8f4a2b82f91cc0e6359d945))
* **torrents:** add `torrents stats` over a canonical measure model ([0b7e800](https://github.com/LECOQQ/qbit-ops/commit/0b7e800e53d58362c95d1119ef69748b91d6fff6))
* **torrents:** add `torrents throttle` for bulk rate limiting ([854d3bf](https://github.com/LECOQQ/qbit-ops/commit/854d3bf80a3a16be29747799c7239dd7b0be4f8a))
* **torrents:** add bulk pause, resume and reannounce ([7fa2c0f](https://github.com/LECOQQ/qbit-ops/commit/7fa2c0f3e476850836ddf73fb40b03257cf153ba))
* **torrents:** add bulk start ([74cce6c](https://github.com/LECOQQ/qbit-ops/commit/74cce6cc0e1febb740dddc4236cd5b286c5160d7))
* **torrents:** add category listing and filtering ([6e8484b](https://github.com/LECOQQ/qbit-ops/commit/6e8484b70b7cfc2894d8b399cf406ee2f57e0a8e))
* **torrents:** add hash-centric selection ([679759f](https://github.com/LECOQQ/qbit-ops/commit/679759fc67ec6715a781f1d3adde0d14e3a71391))
* **torrents:** add HIGH-risk destructive `torrents delete` ([24eb539](https://github.com/LECOQQ/qbit-ops/commit/24eb539a278255ea2e9bd25f240836275decd343))
* **torrents:** add inspect command ([f7c7b27](https://github.com/LECOQQ/qbit-ops/commit/f7c7b27817aa505f1e1330e73261d36e2fe4fce1))
* **torrents:** add name search to inspect command ([ed8e6a2](https://github.com/LECOQQ/qbit-ops/commit/ed8e6a2dc77929ce5547846bae33f44d7f5cf5c5))
* **torrents:** add tracker filter to list command ([112fecd](https://github.com/LECOQQ/qbit-ops/commit/112fecd18383f2c37562ceccb7c213be321f11b3))
* **torrents:** added --all for bulk operations ([b7066b4](https://github.com/LECOQQ/qbit-ops/commit/b7066b4846c42ebf20fb9fe4100af77d6b7fb4c1))
* **torrents:** bound `torrents list` output with --limit ([47e6240](https://github.com/LECOQQ/qbit-ops/commit/47e6240a15035e4bcbfa55432d9014a5ab868706))
* **torrents:** carry per-torrent rate limits on the central model ([7d8b4b5](https://github.com/LECOQQ/qbit-ops/commit/7d8b4b5c678955c847f81d022aa2898d107fa6c8))
* **torrents:** expose the composable cheap filters on `torrents list` ([0d56958](https://github.com/LECOQQ/qbit-ops/commit/0d56958a80da7dbfd2bf0fd4c9f7090b9c38119f))
* **torrents:** filter by tracker presence and excluded tracker hosts ([ffc3ecf](https://github.com/LECOQQ/qbit-ops/commit/ffc3ecfa29b9326e8ff88b61cb3ce49a6cba65be))
* **torrents:** give bulk actions a machine-readable result ([18662eb](https://github.com/LECOQQ/qbit-ops/commit/18662eba23bf891bda2989964bc44ded71bf640d))
* **torrents:** manage categories and tags in bulk ([c5850a6](https://github.com/LECOQQ/qbit-ops/commit/c5850a6bf0f210de771c831152896aafb079bd8e))
* **torrents:** serialize the composable filters and repeat --tracker ([cbcab49](https://github.com/LECOQQ/qbit-ops/commit/cbcab49186e89c7ad4a00e50637d56876ec8bb2a))
* **trackers:** add bulk passkey replacement command ([5080e93](https://github.com/LECOQQ/qbit-ops/commit/5080e9378a3c856182e3196d61ccbe36347a655f))
* **trackers:** add bulk passkey replacement command ([73c788a](https://github.com/LECOQQ/qbit-ops/commit/73c788a8984300bc699b3fd9d30b6787cbf71897))
* **trackers:** add bulk tracker replacement ([2a6da4d](https://github.com/LECOQQ/qbit-ops/commit/2a6da4d3e542454797fbf60c666a17726402ae3c))
* **trackers:** add health audit command ([369bb56](https://github.com/LECOQQ/qbit-ops/commit/369bb565b226e3ab7c908a0cc1770f64e8ad9ba2))
* **trackers:** add inspect, export and verbose output ([6c8513a](https://github.com/LECOQQ/qbit-ops/commit/6c8513a0cc6fc8e29e4c8f2db2d21afabf272a55))
* **trackers:** add structured status reporting ([e865f01](https://github.com/LECOQQ/qbit-ops/commit/e865f01fff8f1ff4b3252d118d7221d8c86de421))
* **trackers:** added exit codes ([e7664e2](https://github.com/LECOQQ/qbit-ops/commit/e7664e234ade0ea2e32309c49a0b1681168523f9))
* **trackers:** make `trackers list` readable on a normal terminal ([9c8b652](https://github.com/LECOQQ/qbit-ops/commit/9c8b652f137d8ec71fba1dd581158d46ddac1007))
* **trackers:** report per-tracker volume and filter by tracker health ([74a28c0](https://github.com/LECOQQ/qbit-ops/commit/74a28c0d40fbc85fbc13d17aaaa8d06be2122496))
* **trackers:** scope add-if-present with torrent filters ([e294f5d](https://github.com/LECOQQ/qbit-ops/commit/e294f5d31fb52a6c8f126ad1af3738c9c4659bf4))
* **trackers:** scope every tracker operation with the composable filters ([c1c4f11](https://github.com/LECOQQ/qbit-ops/commit/c1c4f110926e00e5b6863fee38056f23edd10c68))
* **tui:** add instance-wide lifetime stats to the Overview panel ([8755339](https://github.com/LECOQQ/qbit-ops/commit/8755339e9ae7e71230fdf89ddb00ee036d5ae539))
* **tui:** add low-risk bulk actions ([6f23749](https://github.com/LECOQQ/qbit-ops/commit/6f237493f45c703991cecd7fd35faa3c1c8ac2b1))
* **tui:** add overview-first workspaces ([5a8cba2](https://github.com/LECOQQ/qbit-ops/commit/5a8cba22c1ed9bfcc6e94230261e05d52bfb7ff1))
* **tui:** add read-only torrent dashboard ([0bb46d3](https://github.com/LECOQQ/qbit-ops/commit/0bb46d3e211e2a9caf4b20c15d2859aa5faf1267))
* **tui:** announce a section key the window manager cannot take away ([9c9dd85](https://github.com/LECOQQ/qbit-ops/commit/9c9dd85748791d0ab4321b75f4d66a195e82ac9c))
* **tui:** complete read-only operator dashboard ([0a5d327](https://github.com/LECOQQ/qbit-ops/commit/0a5d32786dcfbeeec53a8bd8af7b62bae6d08635))
* **tui:** finalize torrent workspace and visual identity ([a754165](https://github.com/LECOQQ/qbit-ops/commit/a754165013a8ed67aab516226dd7497ec4f5cbc0))
* **tui:** give every surface one frame, one sheet, one palette ([9a24987](https://github.com/LECOQQ/qbit-ops/commit/9a24987dd3b41d61cbca7c20f6b79613d329f70a))
* **tui:** give the four value actions a modal that collects the argument ([8169f2c](https://github.com/LECOQQ/qbit-ops/commit/8169f2c99d65ac5de3bfaca55eddaa95f70fd4d9))
* **tui:** put all 27 filter fields behind four tabs you commit on Apply ([c46f38b](https://github.com/LECOQQ/qbit-ops/commit/c46f38b2c45d11c0f481b78060bc6ad117d5d6b2))
* **tui:** trace the transfer second by second, in a page that holds still ([4f7c9a9](https://github.com/LECOQQ/qbit-ops/commit/4f7c9a9ea5d52dcafbc38d670c8663c3b9c307c8))
* **tui:** turn the Overview into a live picture of the transfer ([016842f](https://github.com/LECOQQ/qbit-ops/commit/016842fb8e3a31369f54ec82bc97919b2dbd50fd))
* **version:** report qbit-ops, Python and qBittorrent versions ([4a53ea5](https://github.com/LECOQQ/qbit-ops/commit/4a53ea536f6cb1e0518d593f6d00b994796b87b2))


### Bug Fixes

* **ci:** fixed make recipe for ci ([2061e1a](https://github.com/LECOQQ/qbit-ops/commit/2061e1a4a9aa43517e02de43f859f9ed02a45951))
* **cli:** distinguish validation and runtime errors ([41831a4](https://github.com/LECOQQ/qbit-ops/commit/41831a48c25610fb1b61d63270c7ef67b6385fb5))
* **cli:** stop advertising --tracker as repeatable on report commands ([f5d8165](https://github.com/LECOQQ/qbit-ops/commit/f5d8165c9a8122b1fe3a338591f4034f3a3d1e0e))
* **cli:** stop printing a summary that repeats the only finding ([d60a923](https://github.com/LECOQQ/qbit-ops/commit/d60a92376d3ec4adb58cbd0c25a6bacb92694eef))
* **doctor:** remove overbroad qBittorrent support claim ([5d85401](https://github.com/LECOQQ/qbit-ops/commit/5d85401023c80a16aa2c719a4985b69a03ef10c3))
* **execution:** report truthful mutation outcomes ([8500f2e](https://github.com/LECOQQ/qbit-ops/commit/8500f2efe353bdc065a8fa4b49eaa926cbbd26c9))
* **mcp:** make the cap a page size and stop refetching the library ([d86192e](https://github.com/LECOQQ/qbit-ops/commit/d86192ed8e3f1e3df27e81acc3113a24b9a41469))
* **mcp:** stop dropping evidence, limitations and category ([526d61a](https://github.com/LECOQQ/qbit-ops/commit/526d61ac604c31a227b1ad6c908ea2aeaa19a15a))
* **package:** resolve version from installed metadata ([d983e05](https://github.com/LECOQQ/qbit-ops/commit/d983e0571d14444074285ae693ff3e76ac437bc7))
* **qa:** make `make secrets` work on every gitleaks 8.x ([61d2a4d](https://github.com/LECOQQ/qbit-ops/commit/61d2a4d235c8268d4f4cb5fb64a7282933cf35a2))
* **qbit-core:** case-insensitive dedup and blank-hash validation in hash-based ops ([5bb3840](https://github.com/LECOQQ/qbit-ops/commit/5bb3840fca3ae1a02731dcd89e12f41dc8b60bed))
* **release:** restore version checker type safety ([64903c2](https://github.com/LECOQQ/qbit-ops/commit/64903c2150597d7448b706d001b79a1ab9d08f92))
* **security:** redact tracker secrets from all outputs ([b6901bd](https://github.com/LECOQQ/qbit-ops/commit/b6901bd23aefd687d5570c82f5d8cddd6325f503))
* **selection:** make tracker filters crash-proof and selection uniform ([da8f53b](https://github.com/LECOQQ/qbit-ops/commit/da8f53b29d898dca115c8e6139dc96fa7abb4339))
* **test:** run matrix containers as the host user, not a hardcoded uid ([d3fb98d](https://github.com/LECOQQ/qbit-ops/commit/d3fb98d356edff08de3620618e367c7b81bbf285))
* **tests:** disabled forced rich color in CI ([6db95dc](https://github.com/LECOQQ/qbit-ops/commit/6db95dc0bf94250bcb49bafa7a86aa37cbe46efd))
* **tests:** fix cross-command, fix compatibility, doc-consistency ([5bd6d0a](https://github.com/LECOQQ/qbit-ops/commit/5bd6d0adb212ba0a21cf011303b12b8f2957e2c2))
* **tooling:** detect a worktree leak and provision the venv ([f1b57dd](https://github.com/LECOQQ/qbit-ops/commit/f1b57dda18c2ab34aa2c6222839e0146cca0ef7d))
* **torrents:** report the status a bulk mutation actually reached ([d5b6bfe](https://github.com/LECOQQ/qbit-ops/commit/d5b6bfe54b27b9d8ceb2b02d00456e9d6d01206d))
* **trackers:** stop counting DHT/PeX/LSD as trackers ([af28e0e](https://github.com/LECOQQ/qbit-ops/commit/af28e0ec2b26cdcda7a79685994b5961fc78b278))
* **tui:** close remaining mutation lifecycle gaps ([f091c23](https://github.com/LECOQQ/qbit-ops/commit/f091c233db74ed51436933639d81b469616c31e7))
* **tui:** fixed search bar ([559f592](https://github.com/LECOQQ/qbit-ops/commit/559f59200f521f4a34a00ff43b13a7c69aaa3277))
* **tui:** harden bulk mutation lifecycle ([2ddc5c3](https://github.com/LECOQQ/qbit-ops/commit/2ddc5c3ef8d38e0764d7271039dedda6814a6a4f))
* **tui:** keep remote work off the event loop ([d7c5e4a](https://github.com/LECOQQ/qbit-ops/commit/d7c5e4ab75414ff6754ea1e42e2bd05ac4284ded))
* **tui:** leave a value modal by the key everyone already presses ([ba81990](https://github.com/LECOQQ/qbit-ops/commit/ba81990e06a1da7812605576379773289b88b570))
* **tui:** measure the layout that is there, not the one a filter left ([f707391](https://github.com/LECOQQ/qbit-ops/commit/f70739168328cd3d82a7c941c5436a8bfcd50d3f))
* **tui:** polish contextual controls and rendering ([160026c](https://github.com/LECOQQ/qbit-ops/commit/160026c11ac7964103a5b61792ca562e82888607))
* **tui:** repair the missing-extra remediation for PyPI installs ([10da6ab](https://github.com/LECOQQ/qbit-ops/commit/10da6ab1eb791c3461aedd91fd8cea91bdd78c49))
* **tui:** set a window title in plain capitals, not letter by letter ([3b8e103](https://github.com/LECOQQ/qbit-ops/commit/3b8e103c30de6d882ffbc6f3d10041f25ab344b7))
* **tui:** stabilize table events and controls ([6342cf9](https://github.com/LECOQQ/qbit-ops/commit/6342cf93f747b3f2fbee2f82180fc014b5af0c97))
* **tui:** stamp a rate sample with the second that asked for it ([9940f46](https://github.com/LECOQQ/qbit-ops/commit/9940f463a9d0eed54c369c0fbbd25b3080099019))
* **tui:** stop a focus halo from erasing the field it marks ([970e678](https://github.com/LECOQQ/qbit-ops/commit/970e6787c10a73c78a0debaa1966140c4594249c))
* **tui:** stop a stale cell surviving the gesture that cleared it ([eb9660e](https://github.com/LECOQQ/qbit-ops/commit/eb9660e480f7c85790088325da5111a8a0444569))
* **version:** fixed version badge & sync venv ([36ecbb1](https://github.com/LECOQQ/qbit-ops/commit/36ecbb137160f04e85a98cc2a313824f6227e74c))
* **version:** keep --version silent during shell completion ([981785f](https://github.com/LECOQQ/qbit-ops/commit/981785fd1e9d171553778966258e0fee0d235f50))
* **vhs:** update timelines ([b4fb35e](https://github.com/LECOQQ/qbit-ops/commit/b4fb35e4762eb33475547704f48ca0c8b6008a9c))


### Performance Improvements

* **tests:** run the hermetic suites under pytest-xdist ([689205a](https://github.com/LECOQQ/qbit-ops/commit/689205abba0d97d952f01ade6ff601e858338e10))
* **tui:** reduce torrent workspace latency ([d522329](https://github.com/LECOQQ/qbit-ops/commit/d522329c188229ed46c50f94be99a914dc9a32c0))


### Documentation

* add philosophy ([bc1f695](https://github.com/LECOQQ/qbit-ops/commit/bc1f6957ff61bc036a31c8647a70dfbadd27ba37))
* add public product roadmap ([2cbdd5a](https://github.com/LECOQQ/qbit-ops/commit/2cbdd5ac29361d122eb84d4aab372a58363663f0))
* added a changelog ([7431d0d](https://github.com/LECOQQ/qbit-ops/commit/7431d0d0171a37ca889b9cf44669b6ce3f2c1594))
* **compose:** update compose example ([15abebc](https://github.com/LECOQQ/qbit-ops/commit/15abebc51935039e4caa2831ba2c0d3d686f1ae7))
* cut the prose that serves the fewest readers ([ac42b2e](https://github.com/LECOQQ/qbit-ops/commit/ac42b2ed50497fce161489463eff9efef10c58f4))
* **init:** stop explaining the mechanism where nobody asked ([6520c8c](https://github.com/LECOQQ/qbit-ops/commit/6520c8cc19e8c226619ec4c959b2d4397b4b9580))
* **mcp:** present the MCP surface by what it does ([90cf426](https://github.com/LECOQQ/qbit-ops/commit/90cf426332174a42401315180b787976527d8c05))
* point every stale reference at what it actually names, and trim ([4ab0f8d](https://github.com/LECOQQ/qbit-ops/commit/4ab0f8d34cc803fbbe8d05e8604ad96e1311a752))
* point search references at the spec's delivered location ([676ed60](https://github.com/LECOQQ/qbit-ops/commit/676ed60ec4829fac404fa9eb10d50325a4410f0f))
* **readme:** add a *how is qbit-ops different* section ([79e4f02](https://github.com/LECOQQ/qbit-ops/commit/79e4f02eed13830e8d6d0a157cc76810604c5818))
* **readme:** add Homebrew and container badges ([5f266fa](https://github.com/LECOQQ/qbit-ops/commit/5f266fa342dc2aa91ff236d9df1983c28a1f6b26))
* **readme:** add link to roadmap ([80a2784](https://github.com/LECOQQ/qbit-ops/commit/80a27849ce8499b2b909aa41a6598cbdcdef917f))
* **readme:** add PyPI badges and stop inlining the 53 MB demo GIF ([30ea1ca](https://github.com/LECOQQ/qbit-ops/commit/30ea1ca09b638d393b47ec20a5c3ac87792f60d6))
* **readme:** add the Homebrew install for macOS ([d53e5cf](https://github.com/LECOQQ/qbit-ops/commit/d53e5cf9bde0bd1de294ff57e271080205183255))
* **readme:** added status badges ([5d56943](https://github.com/LECOQQ/qbit-ops/commit/5d569436e7bddfbc4915f8273b548382f19535d4))
* **readme:** give each install path its own section ([fd69e2f](https://github.com/LECOQQ/qbit-ops/commit/fd69e2f9d3695c22ae5e7a52c7560f2316f65d21))
* **readme:** group the greatest hits, and name the four tracker moves ([3031a47](https://github.com/LECOQQ/qbit-ops/commit/3031a471890f158f8f669f1a3566645db7c69e18))
* **readme:** update assets, add qbittorrent-cli ([7aa1067](https://github.com/LECOQQ/qbit-ops/commit/7aa10675441f9ae4387786296f702931323ed646))
* **readme:** update readme, add docker distribution, add killer usecases ([672f07b](https://github.com/LECOQQ/qbit-ops/commit/672f07bce997d09a7d16551b1274c09f1bf1ff69))
* **roadmap:** add roadmap_archive, update roadmap w/ latest release ([16da055](https://github.com/LECOQQ/qbit-ops/commit/16da055643495f3dd941602d3666c0878f37ca42))
* **roadmap:** describe capabilities against the code, not the wording ([10282d1](https://github.com/LECOQQ/qbit-ops/commit/10282d1fc695d331e4c87dc4127adc1bbeb17b2b))
* **roadmap:** list every shipped capability under the current release ([60882fc](https://github.com/LECOQQ/qbit-ops/commit/60882fcbdd4a5ecbacc9cb9f1ce93e9598d1ee86))
* **roadmap:** mark the v0.5.0 items that have shipped ([573ad8b](https://github.com/LECOQQ/qbit-ops/commit/573ad8bbcdf14096ef8cd67805656469c3c64a18))
* **roadmap:** reconcile the map with what the code now does ([884efbb](https://github.com/LECOQQ/qbit-ops/commit/884efbb22dafbe7e552e4b20e3fc22bc6638d2f0))
* simplify README and doc set ([e2ae6e2](https://github.com/LECOQQ/qbit-ops/commit/e2ae6e2c9373b8b658b85b11d30cb0b151dea870))
* **testing:** add tracked testing tier policy ([d82c9bc](https://github.com/LECOQQ/qbit-ops/commit/d82c9bc6eb5335983ccdd146f86ca646e791c5f4))
* **tests:** drop references to a document no clone will ever have ([aea0d73](https://github.com/LECOQQ/qbit-ops/commit/aea0d73b5e4f347e5bf8ae0c4bf486e18cd8f8d8))
* **tests:** point the bulk spec reference at its delivered location ([ed7e2e5](https://github.com/LECOQQ/qbit-ops/commit/ed7e2e5d179718b16f19fec43fdf480a4291cd99))
* **tests:** point the throttle suite at the delivered spec ([8446b56](https://github.com/LECOQQ/qbit-ops/commit/8446b5696c0599ee1c2e54cb8f5512f7edf24a2c))
* **torrents:** document bulk rate limiting ([f28b94c](https://github.com/LECOQQ/qbit-ops/commit/f28b94ca7750365f21786c6976e410cd916997fa))
* updated badges and removed version section ([d57d935](https://github.com/LECOQQ/qbit-ops/commit/d57d9358c2da7e57fd985368ee24a675a81323cc))
* **version:** removed version file ([32a7848](https://github.com/LECOQQ/qbit-ops/commit/32a7848ed365720208365d26fc2e71633d436998))

## [0.4.0](https://github.com/LECOQQ/qbit-ops/compare/v0.3.0...v0.4.0) (2026-08-13)


### Features

* **backup:** add additive category/tags/tracker restore from export ([0f4bf77](https://github.com/LECOQQ/qbit-ops/commit/0f4bf77d9c6110997764a84e9347db33532f1f2f))
* **dist:** publish qbit-ops to PyPI via Trusted Publishing ([98d4ae4](https://github.com/LECOQQ/qbit-ops/commit/98d4ae4dd44bbff94d4631f78ed484ad795eadf4))
* **qbit-core:** add pure parsers for operator-typed quantities ([0acc9c9](https://github.com/LECOQQ/qbit-ops/commit/0acc9c94cca3fd338efd09a44515d0b027c74669))
* **qbit-core:** add the composable selection model and its validation ([e9627e1](https://github.com/LECOQQ/qbit-ops/commit/e9627e1ada45f828fcc2a35d5799889508d04774))
* **qbit-core:** evaluate every cheap selection predicate ([77b4ffd](https://github.com/LECOQQ/qbit-ops/commit/77b4ffd4a23f118e3d911cce5b0a91e66ba88f20))
* **qbit-core:** report field absence explicitly for bounded predicates ([4a30f38](https://github.com/LECOQQ/qbit-ops/commit/4a30f38db6e2edf05e56bc0d64062f505624d567))
* **selection:** close the last filter gaps and drop the TUI refresh debt ([e8d3699](https://github.com/LECOQQ/qbit-ops/commit/e8d369926b2d5bb822fab195420784ad6119492f))
* **selection:** filter by completion date, on the observed -1 sentinel ([ad6a7ef](https://github.com/LECOQQ/qbit-ops/commit/ad6a7ef87d9b80085b11c12131a4465091002ecd))
* **selection:** filter by inactivity, on observed last_activity semantics ([78d4d18](https://github.com/LECOQQ/qbit-ops/commit/78d4d185b5e096ac135b7c991563f7ccddc6d389))
* **tooling:** add worktree and documentation-consistency make targets ([7bae8af](https://github.com/LECOQQ/qbit-ops/commit/7bae8af45d2334055fb925c4dc32b870bf9245c5))
* **torrents:** accept the composable filters on every bulk mutation ([dd06b67](https://github.com/LECOQQ/qbit-ops/commit/dd06b67601ad91f910ba95c5d64bf64c82e16ee5))
* **torrents:** add .torrent import (file/dir/zip) via WebUI API ([1446ca3](https://github.com/LECOQQ/qbit-ops/commit/1446ca30902ef2daa9084ae503e8989a9c071eae))
* **torrents:** add HIGH-risk destructive `torrents delete` ([701c549](https://github.com/LECOQQ/qbit-ops/commit/701c549b834a36c0178513f4373ae5457d543028))
* **torrents:** expose the composable cheap filters on `torrents list` ([ef75be3](https://github.com/LECOQQ/qbit-ops/commit/ef75be3f76d5ff49112e20262484bea8cfe6c92e))
* **torrents:** filter by tracker presence and excluded tracker hosts ([2e6d51d](https://github.com/LECOQQ/qbit-ops/commit/2e6d51d371ee1cf05185d6973555d2e60118ee3b))
* **torrents:** serialize the composable filters and repeat --tracker ([91918c1](https://github.com/LECOQQ/qbit-ops/commit/91918c178fe1c858a23c657d4f047b86ac1063aa))
* **trackers:** scope add-if-present with torrent filters ([62490ab](https://github.com/LECOQQ/qbit-ops/commit/62490ab1d298e0a7b8614c7a5632493245f0c143))
* **trackers:** scope every tracker operation with the composable filters ([5701be1](https://github.com/LECOQQ/qbit-ops/commit/5701be1c6ba025211800c237f48201408cfcc8a9))
* **tui:** add instance-wide lifetime stats to the Overview panel ([72a1599](https://github.com/LECOQQ/qbit-ops/commit/72a1599a0044c07496b7e374c615665d59e5f93f))
* **version:** report qbit-ops, Python and qBittorrent versions ([8b3d0e1](https://github.com/LECOQQ/qbit-ops/commit/8b3d0e188d3584c1527f44b8128c172ea32a4692))


### Bug Fixes

* **qbit-core:** case-insensitive dedup and blank-hash validation in hash-based ops ([c5f2fe1](https://github.com/LECOQQ/qbit-ops/commit/c5f2fe1917da2d376b0a22cc0eed8fee627f8def))
* **selection:** make tracker filters crash-proof and selection uniform ([17599df](https://github.com/LECOQQ/qbit-ops/commit/17599df8d562e0a74bdf7da9237fcf10f24d281e))
* **test:** run matrix containers as the host user, not a hardcoded uid ([df68ea8](https://github.com/LECOQQ/qbit-ops/commit/df68ea828fef22b129e7695df4134a37fc51751d))
* **trackers:** stop counting DHT/PeX/LSD as trackers ([cb2e059](https://github.com/LECOQQ/qbit-ops/commit/cb2e0598564b5b43455ac7ca2301a77843ce9384))
* **version:** keep --version silent during shell completion ([a335330](https://github.com/LECOQQ/qbit-ops/commit/a3353308318ecc6e25dc3b4c47450e80a3ad6bb2))


### Performance Improvements

* **tests:** run the hermetic suites under pytest-xdist ([b71a687](https://github.com/LECOQQ/qbit-ops/commit/b71a68749385c54e807e7c48695cb93eac8b93e2))


### Documentation

* add philosophy ([817e7a8](https://github.com/LECOQQ/qbit-ops/commit/817e7a8bb5efc32cac602e9a7871d13efa28dbc9))
* add public product roadmap ([0582b2d](https://github.com/LECOQQ/qbit-ops/commit/0582b2de756f6fdee7946fade94e315a4b457f5d))
* **readme:** add a *how is qbit-ops different* section ([bd18a1f](https://github.com/LECOQQ/qbit-ops/commit/bd18a1febea578dbb5363dd3bdd5d6e5e82f73c9))
* **readme:** add link to roadmap ([6539dd9](https://github.com/LECOQQ/qbit-ops/commit/6539dd9f7d23f433d76393119c50264fafe7a962))
* **readme:** update assets, add qbittorrent-cli ([46ac3eb](https://github.com/LECOQQ/qbit-ops/commit/46ac3eb9248d3636cf8b172559899df388d5c583))

## [0.3.0](https://github.com/LECOQQ/qbit-ops/compare/v0.2.0...v0.3.0) (2026-08-02)


### Features

* **cli:** add progress bars for bulk actions and styled errors ([f8dd5b7](https://github.com/LECOQQ/qbit-ops/commit/f8dd5b772dbb5f1b4d95ed5dfc29e16961ef5a5a))
* **cli:** add Rich output and shell completion ([d16d797](https://github.com/LECOQQ/qbit-ops/commit/d16d797fb9c581205e356b124147c7992f951bd3))
* **cli:** add transient interactive progress ([cf65280](https://github.com/LECOQQ/qbit-ops/commit/cf65280764d0c5f151aa11e73f331660f2c65183))
* **cli:** unify read-only output formats ([e3a3896](https://github.com/LECOQQ/qbit-ops/commit/e3a3896b30f9eac6591d8f5bb9699b673d17f0df))
* **doctor:** add structured diagnostic command ([5075f5f](https://github.com/LECOQQ/qbit-ops/commit/5075f5fb4f534255a15508ffb0f1c0d423030139))
* **doctor:** report exact compatibility evidence ([8bc2293](https://github.com/LECOQQ/qbit-ops/commit/8bc22934b720ecd5830c2f66f5cd6bbb91c06e5f))
* **execution:** add risk-based mutation policies ([63d8d48](https://github.com/LECOQQ/qbit-ops/commit/63d8d48143178366192e78a1f61d7d54aec6280a))
* **explain:** add evidence-based diagnostics ([06ca78d](https://github.com/LECOQQ/qbit-ops/commit/06ca78da61a8d7173f923b7af3f0f1e6a9865361))
* **filters:** unify torrent selection ([a45dbd9](https://github.com/LECOQQ/qbit-ops/commit/a45dbd96743c5f4260fde46a14af9a9ba68e92cc))
* **status:** add live watch mode ([a40128f](https://github.com/LECOQQ/qbit-ops/commit/a40128fc0ac373ac54d9bf34a76cff1561f687a7))
* **status:** add operational snapshot command ([972ea0d](https://github.com/LECOQQ/qbit-ops/commit/972ea0da06efd528fe7b31d84bc84e3fe9d78417))
* **torrents:** add hash-centric selection ([09be5f2](https://github.com/LECOQQ/qbit-ops/commit/09be5f25a7d35f5a207508c477389ca31c83c068))
* **trackers:** add bulk passkey replacement command ([00b875a](https://github.com/LECOQQ/qbit-ops/commit/00b875a86f568e27d014bc444126ddaa67c7527b))
* **trackers:** add bulk passkey replacement command ([c4e7301](https://github.com/LECOQQ/qbit-ops/commit/c4e73013897512261678cf788e73975008c4a8eb))
* **trackers:** add structured status reporting ([46da15d](https://github.com/LECOQQ/qbit-ops/commit/46da15d20b7b6c58195709abf03a1811adb4d499))
* **tui:** add low-risk bulk actions ([37238b5](https://github.com/LECOQQ/qbit-ops/commit/37238b5ee751c251091ad7e0ac9425909968b56f))
* **tui:** add overview-first workspaces ([ec26f4b](https://github.com/LECOQQ/qbit-ops/commit/ec26f4be0fb6275ac0669a2ca322a26baeae1968))
* **tui:** add read-only torrent dashboard ([1786e9d](https://github.com/LECOQQ/qbit-ops/commit/1786e9d56d1f9ebda8836e105112d9bf994c36bb))
* **tui:** complete read-only operator dashboard ([10b7a76](https://github.com/LECOQQ/qbit-ops/commit/10b7a76aac7c1cd5600b45431377b0e9f48a708b))
* **tui:** finalize torrent workspace and visual identity ([6545cc2](https://github.com/LECOQQ/qbit-ops/commit/6545cc2af2699f58ac6210e24b97b362e8888e25))


### Bug Fixes

* **cli:** distinguish validation and runtime errors ([7134b2e](https://github.com/LECOQQ/qbit-ops/commit/7134b2e94d4eeeafa4ba1182d2f08684c2f46262))
* **doctor:** remove overbroad qBittorrent support claim ([9795bb2](https://github.com/LECOQQ/qbit-ops/commit/9795bb20ee483030a8669cee044673124f5ccbc5))
* **execution:** report truthful mutation outcomes ([1b58d0c](https://github.com/LECOQQ/qbit-ops/commit/1b58d0c232d9bf7908ad571bb4916b90deb8cdb9))
* **package:** resolve version from installed metadata ([e9e74f9](https://github.com/LECOQQ/qbit-ops/commit/e9e74f9cd9618f2530212e0fa8377ddb77f45b44))
* **release:** restore version checker type safety ([ac2c71d](https://github.com/LECOQQ/qbit-ops/commit/ac2c71d92b52d6994421e089ee18800c27cfb709))
* **security:** redact tracker secrets from all outputs ([bff703a](https://github.com/LECOQQ/qbit-ops/commit/bff703a7ee5e2d210600a0fb776aedb1fc1a1dc3))
* **tests:** disabled forced rich color in CI ([2da8c26](https://github.com/LECOQQ/qbit-ops/commit/2da8c26703cab5ca268237c1b2b9628af2897aae))
* **tests:** fix cross-command, fix compatibility, doc-consistency ([a4a848e](https://github.com/LECOQQ/qbit-ops/commit/a4a848e74a4cee779d83a63c70ae6d3d9de3d3c2))
* **tui:** close remaining mutation lifecycle gaps ([e5678f4](https://github.com/LECOQQ/qbit-ops/commit/e5678f4256333220fa8b9ded05de4799ae21251f))
* **tui:** fixed search bar ([28d1d5c](https://github.com/LECOQQ/qbit-ops/commit/28d1d5ca0c2fadec6eeb8490048f12dcb842737b))
* **tui:** harden bulk mutation lifecycle ([1079e5a](https://github.com/LECOQQ/qbit-ops/commit/1079e5a452adb32db1b0e5d94a09568ba9a44125))
* **tui:** keep remote work off the event loop ([e5be3eb](https://github.com/LECOQQ/qbit-ops/commit/e5be3eb867efa2eb1a1ca0ee3b486b2665d0badd))
* **tui:** polish contextual controls and rendering ([923428a](https://github.com/LECOQQ/qbit-ops/commit/923428acf8a081e0daf85ac23d59c042d628c74e))
* **tui:** stabilize table events and controls ([5dacac1](https://github.com/LECOQQ/qbit-ops/commit/5dacac16f73caa3870105c588745ebf5f9cd7186))
* **version:** fixed version badge & sync venv ([a987d4f](https://github.com/LECOQQ/qbit-ops/commit/a987d4f542a6affa595475109c930138818b79f3))
* **vhs:** update timelines ([1d283fe](https://github.com/LECOQQ/qbit-ops/commit/1d283fe15108d3f9b512970adc0157d6ea92b6fe))


### Performance Improvements

* **tui:** reduce torrent workspace latency ([c03f723](https://github.com/LECOQQ/qbit-ops/commit/c03f7233dfee761def22104b06e475358127c5be))


### Documentation

* simplify README and doc set ([82f8e0d](https://github.com/LECOQQ/qbit-ops/commit/82f8e0d41adb69a6423b57e2f33a43f117e738a5))
* **testing:** add tracked testing tier policy ([c35ae9d](https://github.com/LECOQQ/qbit-ops/commit/c35ae9d1bb449f56cc61b59a1d78f8034da4cb63))

## [0.2.0](https://github.com/LECOQQ/qbit-ops/compare/v0.1.0...v0.2.0) (2026-06-14)


### Features

* **backup:** add export diff command ([7ed1177](https://github.com/LECOQQ/qbit-ops/commit/7ed1177894dae0d09cadeb2ddbbedcef0f91325d))
* **backup:** add full export and harmonize audit JSON output ([f36dc1c](https://github.com/LECOQQ/qbit-ops/commit/f36dc1c846da43b0f7c40468e33f25fdf3aab88b))
* **cli:** add config doctor and torrent listing ([7e40a40](https://github.com/LECOQQ/qbit-ops/commit/7e40a40c60a08081dc475abe5592c6a7f11339b1))
* **torrents:** add bulk pause, resume and reannounce ([1bb9d70](https://github.com/LECOQQ/qbit-ops/commit/1bb9d70e1f2913dc981b583a5c47cb8595454ec6))
* **torrents:** add bulk start ([a254715](https://github.com/LECOQQ/qbit-ops/commit/a2547153a039175b137f5bda6baaffa1460f6280))
* **torrents:** add category listing and filtering ([33b3d12](https://github.com/LECOQQ/qbit-ops/commit/33b3d12e5c2cf4553a216a6702de804c4cb71448))
* **torrents:** add inspect command ([9758b0d](https://github.com/LECOQQ/qbit-ops/commit/9758b0d68d500af0b9dee13dfd06bed18f1749f1))
* **torrents:** add name search to inspect command ([724b4d0](https://github.com/LECOQQ/qbit-ops/commit/724b4d0f0fa8d527c3f0ae3df8e7e97140db1b19))
* **torrents:** add tracker filter to list command ([2e2d3a5](https://github.com/LECOQQ/qbit-ops/commit/2e2d3a5102701f1e4bd854a89c73f11b27b0694c))
* **torrents:** added --all for bulk operations ([5d6b923](https://github.com/LECOQQ/qbit-ops/commit/5d6b923733c3aed6d839b90cdc36335341b7736f))
* **trackers:** add bulk tracker replacement ([c9ce626](https://github.com/LECOQQ/qbit-ops/commit/c9ce6261496505136c31cac0b074a44400701b04))
* **trackers:** add health audit command ([f4cd938](https://github.com/LECOQQ/qbit-ops/commit/f4cd938216f21bcf64e9aef2492e08dbad625e44))
* **trackers:** add inspect, export and verbose output ([babdd54](https://github.com/LECOQQ/qbit-ops/commit/babdd5460b066b93adfb4358a1dcaabcc1ca5951))
* **trackers:** added exit codes ([5c66cd8](https://github.com/LECOQQ/qbit-ops/commit/5c66cd82be493bea2fcd74fb7842a39620b66768))


### Documentation

* updated badges and removed version section ([74d0e39](https://github.com/LECOQQ/qbit-ops/commit/74d0e3986be81dc9477f16aafc30b6d498bd6387))

## 0.1.0 (2026-06-13)


### Features

* **ci:** added simple ci (check+tests) ([158f004](https://github.com/LECOQQ/qbit-ops/commit/158f004f5d7f517a93a9202931d957164d672075))
* **pipx:** added pipx installation instructions ([c7c08cc](https://github.com/LECOQQ/qbit-ops/commit/c7c08cc5169b20caca2cb9dfdef4796b20a602e5))


### Bug Fixes

* **ci:** fixed make recipe for ci ([dec30e7](https://github.com/LECOQQ/qbit-ops/commit/dec30e77769602fe89526db2696d9ac1c41e3f87))


### Documentation

* added a changelog ([9f9464b](https://github.com/LECOQQ/qbit-ops/commit/9f9464ba37e1f27bfa61534f3908776d1dc64064))
* **readme:** added status badges ([7584677](https://github.com/LECOQQ/qbit-ops/commit/758467731e22b11e4c4b811c2736b9c8faff04f6))
* **version:** removed version file ([682fb30](https://github.com/LECOQQ/qbit-ops/commit/682fb30333a4fc98c1fa3232a124bba02d743cdd))
