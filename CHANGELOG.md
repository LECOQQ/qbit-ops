# Changelog

All notable changes will be documented here, using the [Keep A Changelog](https://keepachangelog.com/en/1.0.0/) formalism,
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)

## [0.6.0](https://github.com/LECOQQ/qbit-ops/compare/v0.5.0...v0.6.0) (2026-09-04)


### ⚠ BREAKING CHANGES

* **sorting:** `trackers list` now orders by torrent count descending instead of by hostname. A script reading its first row gets a different tracker; pass `--sort tracker` to restore the previous order.

### Features

* **cli:** complete --category, --tag and --tracker from a local cache ([125e485](https://github.com/LECOQQ/qbit-ops/commit/125e4852a6b4f4a67cff19bd3ec82f15e75f601a))
* **cli:** complete the closed-enum values shell completion never offered ([425fd4e](https://github.com/LECOQQ/qbit-ops/commit/425fd4e3545b7ceac014c28238837a07779eef23))
* **demo:** render the card GitHub shows when the repo is shared ([8df4f9d](https://github.com/LECOQQ/qbit-ops/commit/8df4f9de01bc9fb831033463de408a96410fef97))
* **install:** add a curl | sh installer wrapping uv ([871e41b](https://github.com/LECOQQ/qbit-ops/commit/871e41b1dd27e57d026fb50ea4c7701e5b4f625c))
* **qa:** catch a passkey that no keyword announces ([3b59732](https://github.com/LECOQQ/qbit-ops/commit/3b597324606c8b0b861e1d2bd4b68131c1cc0b0b))
* **sorting:** add --sort and --desc to torrents list and trackers list ([49f9b08](https://github.com/LECOQQ/qbit-ops/commit/49f9b08dfc9abfad46b2f6890627209b0a33dcf5))
* **tooling:** refuse the two provenance markers that travel with the trailer ([b231c0e](https://github.com/LECOQQ/qbit-ops/commit/b231c0e0d84310a0c23d60850d0b3cf5d6f2c9bb))
* **tui:** let an already-running TUI reconfigure its connection ([fa21271](https://github.com/LECOQQ/qbit-ops/commit/fa2127182bc6c2bef1dee809af6d4d617ba4b348))
* **tui:** sample the graph continuously instead of pausing off Overview ([c4126d4](https://github.com/LECOQQ/qbit-ops/commit/c4126d414570ed01287eb5600ecd029e477cd81c))


### Bug Fixes

* **demo:** render the social card at the ratio that unfurls uncropped ([574972f](https://github.com/LECOQQ/qbit-ops/commit/574972f9c4a0352186acabe62e521f69da68deb9))
* **gallery:** name a tracker that cannot be reached ([2cc546f](https://github.com/LECOQQ/qbit-ops/commit/2cc546f4b70b17f469b8daec852738c055d1a83c))
* **tests:** assert the rate window was replaced, not that it is empty ([3f6e573](https://github.com/LECOQQ/qbit-ops/commit/3f6e573b64174c62b6d525f789e7535f446789b5))
* **tui:** announce the setup form's way out where it has one ([41442d0](https://github.com/LECOQQ/qbit-ops/commit/41442d0272246d7483e57d5cfc462b12a4763932))
* **tui:** capitalise the Overview tracker headings ([d8a755f](https://github.com/LECOQQ/qbit-ops/commit/d8a755f41252bdbaebcdea9b71e575a23f27ebfe))
* **tui:** stop modal up/down from landing on the dialog container ([dcaa879](https://github.com/LECOQQ/qbit-ops/commit/dcaa879319beafab368cd97804e9bb60dddc2d96))


### Performance Improvements

* **refresh:** stop paying twice for the library on every refresh ([017f63d](https://github.com/LECOQQ/qbit-ops/commit/017f63d4368051f3733a9880ca3a3daded447cdb))


### Documentation

* **readme:** cut what explains the build rather than the product ([e368299](https://github.com/LECOQQ/qbit-ops/commit/e3682996e0b5de11efcb4f6a505e7787c6492c0f))
* **readme:** let release-please carry the install URL's version ([0e0a8cb](https://github.com/LECOQQ/qbit-ops/commit/0e0a8cb855bdb2f260f347cdb769b6a36f0632d0))
* **readme:** say that completion exists, and that it survives an upgrade ([47d5a44](https://github.com/LECOQQ/qbit-ops/commit/47d5a44246430331c2a97568a048a996673c02d5))
* **readme:** show the prompt before the pipe, and where the config lands ([34f804d](https://github.com/LECOQQ/qbit-ops/commit/34f804dd914131a97468798179c7683575c09953))
* **roadmap:** move the shipped milestone where shipped milestones go ([dd49cfc](https://github.com/LECOQQ/qbit-ops/commit/dd49cfcb19061df601b327b02e9f5904f9c30b94))
* **roadmap:** name v0.6.0 for what it ships, not for what was planned ([7bed2f0](https://github.com/LECOQQ/qbit-ops/commit/7bed2f0cbc56f429a49a3fc01722f0f01acba964))
* say what the package is on the one line PyPI shows ([075ab16](https://github.com/LECOQQ/qbit-ops/commit/075ab164dd8063988d052dd4867fed8764f70bb0))

## [0.5.0](https://github.com/LECOQQ/qbit-ops/compare/v0.4.0...v0.5.0) (2026-08-24)


### ⚠ BREAKING CHANGES

* **trackers:** `trackers remove --match` is removed. Tracker identification no longer consults it, and `remove` has no target to compare against, so the option governed nothing. `--source` and `--tracker` now take a host, or a `{passkey}` template, instead of a full announce URL.
* **search:** `torrents inspect --name` and `--limit` are removed with no alias -- `torrents search` replaces them. In search output, `match_score` becomes `match`, the tier name; `summary.matched` now counts before truncation, alongside `returned` and `truncated`, so it answers "how many are there" rather than "how many did you print".

### Features

* **config:** guide a fresh install to its first working command ([7477896](https://github.com/LECOQQ/qbit-ops/commit/747789602b10f0c7e88ebc19c30ba113b8925242))
* **demo:** give every fixture a tracker, live and dead ([b6df70a](https://github.com/LECOQQ/qbit-ops/commit/b6df70aeb81fa4774d18466d6c2340a82a348729))
* **demo:** give the Overview graph a real, reproducible transfer ([d135722](https://github.com/LECOQQ/qbit-ops/commit/d1357225b8857579331daa0e20e884c8d8d08e33))
* **docker:** publish multi-arch container images to GHCR ([9c129e0](https://github.com/LECOQQ/qbit-ops/commit/9c129e0d9f0bcac4f75236e0e626bb6ebd13908d))
* **mcp:** add an aggregate tool over a filtered selection ([a5176dc](https://github.com/LECOQQ/qbit-ops/commit/a5176dc11c40fbabc12c8185f797a21b36b2a331))
* **mcp:** add an experimental read-only MCP surface ([f5e561b](https://github.com/LECOQQ/qbit-ops/commit/f5e561b8f6798263890d4c469121ae4912611f12))
* **search:** add `torrents search` and make the TUI `/` tolerant ([f5ef07f](https://github.com/LECOQQ/qbit-ops/commit/f5ef07fe89948b42d9bbd95dad284385f7d1d28e))
* **search:** add the ranking engine behind `torrents search` ([5b1fc28](https://github.com/LECOQQ/qbit-ops/commit/5b1fc283d66c31dc3645b4be989d17a09ff6c42f))
* **stats:** read a cumulative seed time in conventional units ([b32936b](https://github.com/LECOQQ/qbit-ops/commit/b32936bdcfeabb44f86f099e72e4fdda042ed8cb))
* **status,explain:** stop flagging a stall completed from local data ([98fce0a](https://github.com/LECOQQ/qbit-ops/commit/98fce0af11018216e5cd95d1fb8f04eb21ab935f))
* **tooling:** check references inside code prose, not only Markdown ([e7bc498](https://github.com/LECOQQ/qbit-ops/commit/e7bc498d57f488fb98c03a95dd7f666272fefbdc))
* **tooling:** enforce commit provenance and prove squash integration ([33f54ef](https://github.com/LECOQQ/qbit-ops/commit/33f54ef4a092094318713b49530bb89eee16d544))
* **tooling:** gate AI hygiene and break the diff down by kind of work ([7fa358f](https://github.com/LECOQQ/qbit-ops/commit/7fa358feb8e3396680a3e80a7267c8297e881ec6))
* **tooling:** give each feature worktree its own proven virtualenv ([7208082](https://github.com/LECOQQ/qbit-ops/commit/7208082447c800b0afa9603a6ed67db1f2ebf0be))
* **tooling:** isolate the test suite from the operator's own instance ([4d84361](https://github.com/LECOQQ/qbit-ops/commit/4d843611d267561e7c957a311e5acbd67b5b09ea))
* **tooling:** refuse a commit body written as prose ([f988518](https://github.com/LECOQQ/qbit-ops/commit/f9885180ac27ccd5b701b64023f8d111f4168f61))
* **torrents:** add `torrents stats` over a canonical measure model ([0b7e800](https://github.com/LECOQQ/qbit-ops/commit/0b7e800e53d58362c95d1119ef69748b91d6fff6))
* **torrents:** add `torrents throttle` for bulk rate limiting ([854d3bf](https://github.com/LECOQQ/qbit-ops/commit/854d3bf80a3a16be29747799c7239dd7b0be4f8a))
* **torrents:** bound `torrents list` output with --limit ([47e6240](https://github.com/LECOQQ/qbit-ops/commit/47e6240a15035e4bcbfa55432d9014a5ab868706))
* **torrents:** carry per-torrent rate limits on the central model ([7d8b4b5](https://github.com/LECOQQ/qbit-ops/commit/7d8b4b5c678955c847f81d022aa2898d107fa6c8))
* **torrents:** give bulk actions a machine-readable result ([18662eb](https://github.com/LECOQQ/qbit-ops/commit/18662eba23bf891bda2989964bc44ded71bf640d))
* **torrents:** manage categories and tags in bulk ([c5850a6](https://github.com/LECOQQ/qbit-ops/commit/c5850a6bf0f210de771c831152896aafb079bd8e))
* **trackers:** make `trackers list` readable on a normal terminal ([9c8b652](https://github.com/LECOQQ/qbit-ops/commit/9c8b652f137d8ec71fba1dd581158d46ddac1007))
* **trackers:** name a tracker without naming its passkey ([185b136](https://github.com/LECOQQ/qbit-ops/commit/185b13690b68e8a702e035616bb82b8b3dae8169))
* **trackers:** report per-tracker volume and filter by tracker health ([74a28c0](https://github.com/LECOQQ/qbit-ops/commit/74a28c0d40fbc85fbc13d17aaaa8d06be2122496))
* **tui:** announce a section key the window manager cannot take away ([9c9dd85](https://github.com/LECOQQ/qbit-ops/commit/9c9dd85748791d0ab4321b75f4d66a195e82ac9c))
* **tui:** announce the select-visible key in the command bar ([dc6154c](https://github.com/LECOQQ/qbit-ops/commit/dc6154cc3d6624facbccb7173d54fe3c8989c3e6))
* **tui:** give every surface one frame, one sheet, one palette ([9a24987](https://github.com/LECOQQ/qbit-ops/commit/9a24987dd3b41d61cbca7c20f6b79613d329f70a))
* **tui:** give the four value actions a modal that collects the argument ([8169f2c](https://github.com/LECOQQ/qbit-ops/commit/8169f2c99d65ac5de3bfaca55eddaa95f70fd4d9))
* **tui:** put all 27 filter fields behind four tabs you commit on Apply ([c46f38b](https://github.com/LECOQQ/qbit-ops/commit/c46f38b2c45d11c0f481b78060bc6ad117d5d6b2))
* **tui:** trace the transfer second by second, in a page that holds still ([4f7c9a9](https://github.com/LECOQQ/qbit-ops/commit/4f7c9a9ea5d52dcafbc38d670c8663c3b9c307c8))
* **tui:** turn the Overview into a live picture of the transfer ([016842f](https://github.com/LECOQQ/qbit-ops/commit/016842fb8e3a31369f54ec82bc97919b2dbd50fd))


### Bug Fixes

* **cli,mcp:** let a caller read the tags it just filtered on ([ab41ffe](https://github.com/LECOQQ/qbit-ops/commit/ab41ffe7f647f127343edb3c80e4627f58596491))
* **cli:** name the summary row for what it holds ([f633a9e](https://github.com/LECOQQ/qbit-ops/commit/f633a9eb403508b61666f1220ae53375cd566170))
* **cli:** stop advertising --tracker as repeatable on report commands ([f5d8165](https://github.com/LECOQQ/qbit-ops/commit/f5d8165c9a8122b1fe3a338591f4034f3a3d1e0e))
* **cli:** stop printing a summary that repeats the only finding ([d60a923](https://github.com/LECOQQ/qbit-ops/commit/d60a92376d3ec4adb58cbd0c25a6bacb92694eef))
* **config,trackers:** stop a secret reaching a place it was never meant to ([b205b01](https://github.com/LECOQQ/qbit-ops/commit/b205b01f5b36a14ac0f38974a49f8e766696aa01))
* **core,cli,tui:** hold the contract when a third party controls the input ([c2c49f8](https://github.com/LECOQQ/qbit-ops/commit/c2c49f813ce20fc9a03c8b4d44ada9b828fa1091))
* **demo:** let the stills show a library, not a single row ([6cbdce0](https://github.com/LECOQQ/qbit-ops/commit/6cbdce08e0c245962e9277bed651195bb851c2ef))
* **demo:** record a run that shows what it claims to show ([b1e4341](https://github.com/LECOQQ/qbit-ops/commit/b1e43411fb128882aef70b5dc6181a4d641bedbd))
* **demo:** stop pointing the docs gate at a directory that is not there ([08ca988](https://github.com/LECOQQ/qbit-ops/commit/08ca988c3ba6ca73ea07c9f2122bd1fb22d088d0))
* **gates:** let five checks fail on what they were written to catch ([dc61e61](https://github.com/LECOQQ/qbit-ops/commit/dc61e6178ec100dc8ef93049b36f909394d96336))
* **mcp:** answer from the same engine the CLI answers from ([c2afbb2](https://github.com/LECOQQ/qbit-ops/commit/c2afbb217f4a5b2971db277703060a196027ea0e))
* **mcp:** make the cap a page size and stop refetching the library ([d86192e](https://github.com/LECOQQ/qbit-ops/commit/d86192ed8e3f1e3df27e81acc3113a24b9a41469))
* **mcp:** stop dropping evidence, limitations and category ([526d61a](https://github.com/LECOQQ/qbit-ops/commit/526d61ac604c31a227b1ad6c908ea2aeaa19a15a))
* **qa:** make `make secrets` work on every gitleaks 8.x ([61d2a4d](https://github.com/LECOQQ/qbit-ops/commit/61d2a4d235c8268d4f4cb5fb64a7282933cf35a2))
* **release:** re-anchor release-please after the history rewrite ([f02d308](https://github.com/LECOQQ/qbit-ops/commit/f02d308c7e66c2f775cb5f7ae874980fd9a17dc4))
* **secrets:** stop scanning the one file that logs arbitrary commands ([747ab1d](https://github.com/LECOQQ/qbit-ops/commit/747ab1d028129c5a5c83ff8acdb0de850ea6cf0d))
* **tooling:** detect a worktree leak and provision the venv ([f1b57dd](https://github.com/LECOQQ/qbit-ops/commit/f1b57dda18c2ab34aa2c6222839e0146cca0ef7d))
* **torrents:** report the status a bulk mutation actually reached ([d5b6bfe](https://github.com/LECOQQ/qbit-ops/commit/d5b6bfe54b27b9d8ceb2b02d00456e9d6d01206d))
* **tui,docs:** derive the numbers that were transcribed by hand ([04ef44c](https://github.com/LECOQQ/qbit-ops/commit/04ef44cd56889b48b2530111c97ba14f72cb5e90))
* **tui:** leave a value modal by the key everyone already presses ([ba81990](https://github.com/LECOQQ/qbit-ops/commit/ba81990e06a1da7812605576379773289b88b570))
* **tui:** make delete inexpressible, not merely unimported ([fcbc680](https://github.com/LECOQQ/qbit-ops/commit/fcbc680f7d04c870b94e91e34803d51df0c26ea7))
* **tui:** measure the layout that is there, not the one a filter left ([f707391](https://github.com/LECOQQ/qbit-ops/commit/f70739168328cd3d82a7c941c5436a8bfcd50d3f))
* **tui:** repair the missing-extra remediation for PyPI installs ([10da6ab](https://github.com/LECOQQ/qbit-ops/commit/10da6ab1eb791c3461aedd91fd8cea91bdd78c49))
* **tui:** set a window title in plain capitals, not letter by letter ([3b8e103](https://github.com/LECOQQ/qbit-ops/commit/3b8e103c30de6d882ffbc6f3d10041f25ab344b7))
* **tui:** stamp a rate sample with the second that asked for it ([9940f46](https://github.com/LECOQQ/qbit-ops/commit/9940f463a9d0eed54c369c0fbbd25b3080099019))
* **tui:** stop a focus halo from erasing the field it marks ([970e678](https://github.com/LECOQQ/qbit-ops/commit/970e6787c10a73c78a0debaa1966140c4594249c))
* **tui:** stop a stale cell surviving the gesture that cleared it ([eb9660e](https://github.com/LECOQQ/qbit-ops/commit/eb9660e480f7c85790088325da5111a8a0444569))


### Performance Improvements

* **qbit-core,tui:** stop paying for work no filter ever asked for ([6050f66](https://github.com/LECOQQ/qbit-ops/commit/6050f666ec9a113e12b2cf756084bd3cd1469284))
* **trackers:** collapse the tracker scan when the server embeds them ([5e6c2ca](https://github.com/LECOQQ/qbit-ops/commit/5e6c2cafaf173e848944982949d11b7c9b795b90))
* **tui:** debounce the search so a burst costs one recompute, not nine ([a7640d9](https://github.com/LECOQQ/qbit-ops/commit/a7640d9986ac372014ffdf96e0ec1d61bb6404ec))


### Documentation

* **compose:** update compose example ([15abebc](https://github.com/LECOQQ/qbit-ops/commit/15abebc51935039e4caa2831ba2c0d3d686f1ae7))
* cut the prose that serves the fewest readers ([ac42b2e](https://github.com/LECOQQ/qbit-ops/commit/ac42b2ed50497fce161489463eff9efef10c58f4))
* **demo:** say what the environment is now, not what it was ([d8b945f](https://github.com/LECOQQ/qbit-ops/commit/d8b945f476de890e9864958dcdc67d39b7de4140))
* **init:** stop explaining the mechanism where nobody asked ([6520c8c](https://github.com/LECOQQ/qbit-ops/commit/6520c8cc19e8c226619ec4c959b2d4397b4b9580))
* **mcp:** present the MCP surface by what it does ([90cf426](https://github.com/LECOQQ/qbit-ops/commit/90cf426332174a42401315180b787976527d8c05))
* **mcp:** put the third surface in the document that describes them ([693b598](https://github.com/LECOQQ/qbit-ops/commit/693b5984cacc6bb521742c689d453687f43ef57d))
* open a backlog for known issues that were deferred ([75a46da](https://github.com/LECOQQ/qbit-ops/commit/75a46da41bac345a4d2eb80c0f6b0644ba6a790f))
* point every stale reference at what it actually names, and trim ([4ab0f8d](https://github.com/LECOQQ/qbit-ops/commit/4ab0f8d34cc803fbbe8d05e8604ad96e1311a752))
* point search references at the spec's delivered location ([676ed60](https://github.com/LECOQQ/qbit-ops/commit/676ed60ec4829fac404fa9eb10d50325a4410f0f))
* **readme:** add Homebrew and container badges ([5f266fa](https://github.com/LECOQQ/qbit-ops/commit/5f266fa342dc2aa91ff236d9df1983c28a1f6b26))
* **readme:** add PyPI badges and stop inlining the 53 MB demo GIF ([30ea1ca](https://github.com/LECOQQ/qbit-ops/commit/30ea1ca09b638d393b47ec20a5c3ac87792f60d6))
* **readme:** add the Homebrew install for macOS ([d53e5cf](https://github.com/LECOQQ/qbit-ops/commit/d53e5cf9bde0bd1de294ff57e271080205183255))
* **readme:** give each install path its own section ([fd69e2f](https://github.com/LECOQQ/qbit-ops/commit/fd69e2f9d3695c22ae5e7a52c7560f2316f65d21))
* **readme:** group the greatest hits, and name the four tracker moves ([3031a47](https://github.com/LECOQQ/qbit-ops/commit/3031a471890f158f8f669f1a3566645db7c69e18))
* **readme:** play the demo instead of linking to it ([48f9929](https://github.com/LECOQQ/qbit-ops/commit/48f9929e1266f5900222e12268c9094e4a464a73))
* **readme:** show the product, stop listing its parts ([fffbe01](https://github.com/LECOQQ/qbit-ops/commit/fffbe018018c8e17086b719dceb47641b5efae9e))
* **readme:** update readme, add docker distribution, add killer usecases ([672f07b](https://github.com/LECOQQ/qbit-ops/commit/672f07bce997d09a7d16551b1274c09f1bf1ff69))
* **roadmap:** add roadmap_archive, update roadmap w/ latest release ([16da055](https://github.com/LECOQQ/qbit-ops/commit/16da055643495f3dd941602d3666c0878f37ca42))
* **roadmap:** describe capabilities against the code, not the wording ([10282d1](https://github.com/LECOQQ/qbit-ops/commit/10282d1fc695d331e4c87dc4127adc1bbeb17b2b))
* **roadmap:** list every shipped capability under the current release ([60882fc](https://github.com/LECOQQ/qbit-ops/commit/60882fcbdd4a5ecbacc9cb9f1ce93e9598d1ee86))
* **roadmap:** mark the v0.5.0 items that have shipped ([573ad8b](https://github.com/LECOQQ/qbit-ops/commit/573ad8bbcdf14096ef8cd67805656469c3c64a18))
* **roadmap:** put each line where the code says it belongs ([17c971e](https://github.com/LECOQQ/qbit-ops/commit/17c971e8f6485aa47e11676d074b1477684c1d9e))
* **roadmap:** reconcile the map with what the code now does ([884efbb](https://github.com/LECOQQ/qbit-ops/commit/884efbb22dafbe7e552e4b20e3fc22bc6638d2f0))
* **source:** say what the code does, and say it once ([13ad3d6](https://github.com/LECOQQ/qbit-ops/commit/13ad3d61e2d9b0fe7e051fe71bf677a6d34e9018))
* **tests:** drop a numbering no document in this repository defines ([c50a630](https://github.com/LECOQQ/qbit-ops/commit/c50a6309d43b7a0923c966d93b05bbf782f3db1b))
* **tests:** drop references to a document no clone will ever have ([aea0d73](https://github.com/LECOQQ/qbit-ops/commit/aea0d73b5e4f347e5bf8ae0c4bf486e18cd8f8d8))
* **tests:** let the test name carry the scenario, as the budget says ([744a050](https://github.com/LECOQQ/qbit-ops/commit/744a050af8d44b7333a3498c401ceddc0f05c11d))
* **tests:** point the bulk spec reference at its delivered location ([ed7e2e5](https://github.com/LECOQQ/qbit-ops/commit/ed7e2e5d179718b16f19fec43fdf480a4291cd99))
* **tests:** point the throttle suite at the delivered spec ([8446b56](https://github.com/LECOQQ/qbit-ops/commit/8446b5696c0599ee1c2e54cb8f5512f7edf24a2c))
* **torrents:** document bulk rate limiting ([f28b94c](https://github.com/LECOQQ/qbit-ops/commit/f28b94ca7750365f21786c6976e410cd916997fa))

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
