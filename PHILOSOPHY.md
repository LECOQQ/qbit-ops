# 🧭 Philosophy

## 🌱 Why `qbit-ops` exists

Everything started with a very ordinary problem: I had more than 1150 torrents in qBittorrent, and managing them by hand had stopped being practical.

The WebUI is great when you need to inspect or change a few torrents. At a thousand-plus torrents, the problem changes - you start wanting to:
- find very specific subsets;
- inspect patterns across the whole library;
- repair tracker URLs;
- change categories or tags in bulk;
- reannounce or pause selected torrents;
- script repetitive maintenance.

At that point, automation becomes attractive, but it creates a second problem: the blast radius grows dramatically.

A typo, an overly broad selector, or one bad loop can affect hundreds or thousands of torrents instantly.

That tension is where `qbit-ops` comes from:

> **I wanted the power of scripting without having to trust every bulk operation to the god of luck.**

The project therefore exists to make large qBittorrent libraries both **scriptable** and **safe to operate**.

---

## 🎯 What qbit-ops is

`qbit-ops` is an operational layer on top of qBittorrent.

Its main job is to help power users select torrents precisely, inspect and understand their state, diagnose problems, preview and apply bulk changes safely, automate repetitive tasks and manage libraries that are too large to operate comfortably by hand. 

The CLI is the primary automation interface. The TUI is an interactive interface over the same core semantics. The reusable `qbit-core` modules exist so features do not have to rebuild qBittorrent operational logic independently.

---

## 🧯 Power users should not need footguns

The intended user is comfortable with terminals, scripting, homelabs, seedboxes and large torrent libraries.

That does not mean unsafe defaults are acceptable. Quite the opposite:
> The larger the blast radius, the more explicit and predictable the operation should become.

This leads to a few project rules:
- mutations are dry-run-first;
- an empty selector never means "everything";
- ambiguous targeting fails before mutation;
- unknown data must not silently broaden a selection;
- what you preview is what gets applied.

Safety is a part of the execution model, not an optional mode.

## 🎯 Selection comes before action

At scale, the hard question is often not:
> "How do I pause torrents?"
But rather:
> "Exactly which torrents do I want to pause?"

Therefore, selection should be independent from the action performed afterwards and should be reusable for every mutation.

## ✅ A simple test for new features

A feature probably belongs in `qbit-ops` if it helps someone:
> **select, inspect, understand, plan or safely operate on a qBittorrent library at a scale where doing it manually becomes painful.**
