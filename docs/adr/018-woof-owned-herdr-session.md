---
type: adr
status: accepted
date: 2026-07-13
---

# ADR-018: Woof declares and owns the herdr named session it dispatches into

## Context

ADR-012 puts every worker behind the backend its harness profile declares, and settles that herdr compatibility is determined from the running server reached through the profile's named-session socket. It does not say which named session Woof dispatches into, or who owns it.

That gap matters because of an asymmetry between the two backends. Under tmux the client spawns the worker process, so the worker inherits the client's environment and dies with the session the client created. Under herdr the *server* spawns the worker: the client asks a named session's server to start an agent, and the worker then belongs to that server. Two consequences follow. A session Woof merely borrows is a session Woof cannot safely reap or tear down, because other work lives in it. And a session Woof assumes rather than declares is whichever server happens to be listening, which on this machine is the operator's shared `drains` session carrying live drains.

Two failures on 2026-07-12 make the ownership question concrete rather than theoretical. A herdr server died and left its socket file on disk; because the socket file was treated as the session, every subsequent dispatch failed with a connection refusal that never self-healed until the socket was deleted by hand. Separately, a dispatch client was killed and its herdr worker kept running, detached, so a replacement dispatch put two workers in one working tree editing the same files. Both are recoverable only by an owner: reaping an orphaned socket and killing a stray worker are destructive acts, and Woof may not perform them on a session whose other occupants it knows nothing about.

## Decision

Woof declares the herdr named session it dispatches into, and owns it.

The session is named explicitly through `WOOF_HERDR_SESSION`. There is no implicit default. A herdr-backed profile dispatched with no declared session is refused before any worker starts.

The operator's own sessions (`default`, `drains`) are refused as dispatch targets and as teardown targets. Woof starts no worker in them and stops neither their workers nor their server.

Ownership carries the recovery duties. Woof probes liveness by pinging the server, not by looking for the socket file; a socket with no server answering behind it is a dead session, whose leftover server and client sockets Woof reaps before respawning the server. Reaping is destructive, so death is confirmed by repeated failed probes rather than assumed from one. Every worker Woof launches is launched under a stable name and its reference recorded on disk as soon as the worker exists, and Woof terminates the workers it launched, so a worker that outlived its client is found by its name rather than by guessing at a process id -- both to reattach to it and to kill it.

Protocol work and live smokes use a disposable named session on its own socket, torn down afterwards.

## Consequences

- An operator must declare a session before a herdr-backed drain runs. Preflight reports the declared session's running server; it stays silent when none is declared, because dispatch is the authority there and refuses loudly.
- A Woof drain can never colonise, disturb, or take down the operator's live sessions, and a Woof test run cannot reach them at all.
- The orphaned-socket failure self-heals: dispatch reaps and respawns. Preflight reports a dead session as a warning rather than a blocker, because dispatch recovers it. Only an incompatible protocol fails preflight.
- Woof's teardown may safely close every worker in its session and stop the server, which is what makes an isolated named-session smoke possible.
- The tmux backend is unaffected: its worker reference is the session it created, and it has no server to share.
