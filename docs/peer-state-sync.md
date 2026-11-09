# Unison Local Peer State Synchronization

## 1. Goals and non-goals

This module synchronizes user-owned notification state between trusted Unison
installations on the same local network:

- discover peers with multicast DNS (mDNS);
- authenticate every connection with mutually authenticated TLS;
- synchronize only state transitions and the minimum notification identity
  required to apply them;
- converge after packet loss, duplication, reordering, reconnects, and offline
  edits;
- never require a cloud relay or upload notification content.

The first protocol version synchronizes `dismissed`, `archived`, and `snoozed`
state. Raw notification bodies, embeddings, model outputs, and credentials are
not replicated by default.

This is a **state replication** protocol, not a notification transport. A peer
may miss the original notification and still converge its local state when it
later receives a valid state record.

## 2. Component architecture

```text
TypeScript UI / platform adapter
            |
            v
       SyncCoordinator
            |
     Rust sync core (UniFFI/N-API)
       /          |           \
  MdnsAgent   TlsTransport   CrdtStore
       |          |             |
  discovery   mTLS streams   SQLite state + outbox
```

### Responsibilities

- **`MdnsAgent`**: browse and publish `_unison-sync._tcp.local`; validate TXT
  metadata before attempting a connection.
- **`TlsTransport`**: establish TLS 1.3 mutual authentication, frame messages,
  enforce limits, and expose reconnect/error states.
- **`CrdtStore`**: persist versioned state registers, tombstones, peer cursors,
  and unsent local deltas transactionally.
- **`SyncCoordinator`**: schedule sessions, exchange digests, send missing
  records, merge received state, and publish UI changes.
- **UI/platform adapter**: pairing UX, permission prompts, network status, and
  user-visible recovery actions; it never handles private keys directly.

The Rust core is authoritative for protocol and merge behavior. TypeScript
bindings are a typed facade and must not reimplement CRDT ordering.

## 3. Peer identity and pairing

### 3.1 Stable identity

Each installation creates a device identity once:

- Ed25519 signing key pair for peer identity and protocol signatures;
- private key stored in the platform secure keystore:
  Android Keystore, Windows DPAPI/Credential Manager, or Linux Secret Service;
- self-signed certificate containing the Ed25519 public key and a random
  installation ID;
- certificate fingerprint shown as a short human-verifiable pairing code.

The certificate is not trusted merely because it was discovered through mDNS.
Discovery is unauthenticated metadata.

### 3.2 Pairing

Pairing is an explicit user action on both devices:

1. Device A advertises its installation ID and certificate fingerprint.
2. Device B displays the fingerprint and generates a one-time pairing code.
3. The user confirms the same code on A, or scans a local QR payload.
4. The devices exchange certificates over the initial TLS channel.
5. Each side stores the other certificate fingerprint in `trusted_peers`.
6. Future handshakes require the stored fingerprint and certificate public key.

Unpaired peers may be visible in the UI but cannot synchronize. A peer can be
revoked locally; revoked fingerprints are rejected before CRDT processing.

## 4. mDNS discovery

### 4.1 Service record

Publish:

```text
_unison-sync._tcp.local
```

Example records:

```text
unison-7f2a._unison-sync._tcp.local.  SRV  0 0 48732 unison-7f2a.local.
unison-7f2a._unison-sync._tcp.local.  TXT  v=1
                                          id=9a4f...
                                          fp=sha256:ABCD...
                                          proto=1
                                          caps=crdt-v1,delta,zstd
                                          expires=1731168000
unison-7f2a.local.                    A    192.168.1.42
```

Rules:

- bind only to a private/local interface; never advertise the service on a
  public interface or VPN interface unless explicitly enabled;
- TXT `id`, `fp`, and `proto` are hints and must be checked against the TLS
  certificate and trusted-peer table;
- ignore records with expired `expires`, unsupported protocol versions, invalid
  IDs, or ports outside the valid TCP range;
- cap discovered peers and resolve addresses with a timeout;
- do not place notification text, sender names, or account identifiers in TXT
  records;
- use randomized instance names to reduce passive device correlation.

Discovery is opportunistic. A configured peer address and anti-entropy retry
must continue to work when mDNS is blocked by a router, firewall, or OS
permission.

## 5. TLS transport

### 5.1 Handshake requirements

Use TLS 1.3 only. Both sides must:

- present a certificate signed by the local Unison pairing authority or a
  pinned self-signed certificate;
- verify the peer certificate fingerprint against `trusted_peers`;
- verify the certificate's installation ID matches the mDNS `id`;
- negotiate an application protocol such as `unison-sync/1`;
- reject plaintext, TLS 1.2 fallback, unknown ALPN, and invalid certificate
  key usage;
- disable compression at the TLS layer.

TLS provides confidentiality and channel integrity. Protocol messages are also
signed by the sender's Ed25519 key so a valid state record can be audited after
storage and replayed safely.

### 5.2 Framing and limits

Use a length-delimited frame:

```text
u32 big-endian payload length
u8  message type
bytes canonical CBOR payload
bytes Ed25519 signature
```

Limits for version 1:

```text
maximum frame:       256 KiB
maximum records:     512 per delta message
maximum session:     30 seconds idle
maximum in-flight:   4 messages
maximum decompressed: 1 MiB per message
```

CBOR is preferred for the wire format because it has a canonical encoding and
compact binary representation. JSON may be used by a debug tool, but it is not
accepted on the production protocol endpoint.

## 6. TypeScript protocol schema

The following types describe the public binding. The Rust implementation is
the serialization and validation authority.

```ts
export type PeerId = string;       // lowercase UUID/installation ID
export type NotificationId = string;
export type Hlc = string;          // uint64 physical millis + logical counter
export type Hash256 = string;      // lowercase hex SHA-256

export type NotificationState =
  | { kind: "active" }
  | { kind: "dismissed"; at: Hlc }
  | { kind: "archived"; at: Hlc }
  | { kind: "snoozed"; until: string; at: Hlc }; // UTC ISO-8601

export interface StateVersion {
  hlc: Hlc;
  actor: PeerId;
  operationId: string;             // UUID; idempotency key
}

export interface NotificationStateRecord {
  notificationId: NotificationId;
  state: NotificationState;
  version: StateVersion;
  deleted?: boolean;               // tombstone, never omitted when true
}

export interface SyncDigest {
  protocol: 1;
  peerId: PeerId;
  sessionId: string;
  stateRoot: Hash256;
  recordCount: number;
  bucketRoots: Record<string, Hash256>; // YYYYMMDDHH buckets
  maxHlc: Hlc;
}

export interface SyncRequest {
  requestId: string;
  want: "digest" | "records" | "ack";
  bucket?: string;
  ids?: NotificationId[];
  after?: StateVersion;
  limit?: number;
}

export interface SyncDelta {
  requestId: string;
  records: NotificationStateRecord[];
  more: boolean;
  cursor?: string;
}

export interface SyncAck {
  requestId: string;
  accepted: NotificationId[];
  rejected: Array<{ notificationId: NotificationId; reason: string }>;
  stateRoot: Hash256;
}

export type SyncErrorCode =
  | "unauthorized" | "protocol_mismatch" | "frame_too_large"
  | "rate_limited" | "busy" | "invalid_signature"
  | "invalid_record" | "temporary_unavailable";
```

`Hlc` is serialized as a fixed-width decimal string
`physical_millis << 16 | logical_counter`. A receiver must reject malformed,
overflowing, or future-skewed values beyond the configured clock-skew bound.

## 7. Rust core schema and traits

```rust
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Serialize, Deserialize, Eq, PartialEq, Hash)]
pub struct PeerId(pub String);

#[derive(Clone, Debug, Serialize, Deserialize, Eq, PartialEq)]
pub struct Version {
    pub hlc: u128,
    pub actor: PeerId,
    pub operation_id: uuid::Uuid,
}

#[derive(Clone, Debug, Serialize, Deserialize, Eq, PartialEq)]
pub enum NotificationState {
    Active,
    Dismissed { at: u128 },
    Archived { at: u128 },
    Snoozed { until: DateTime<Utc>, at: u128 },
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct StateRecord {
    pub notification_id: String,
    pub state: NotificationState,
    pub version: Version,
    pub tombstone: bool,
}

pub trait StateCrdt: Send + Sync {
    fn apply(&mut self, record: StateRecord) -> ApplyResult;
    fn merge(&mut self, records: impl IntoIterator<Item = StateRecord>) -> MergeStats;
    fn digest(&self) -> Digest;
}

pub trait PeerTransport: Send + Sync {
    async fn connect(&self, peer: &DiscoveredPeer) -> Result<Session, SyncError>;
    async fn exchange(&self, session: &mut Session) -> Result<(), SyncError>;
}

pub trait DurableStateStore: Send + Sync {
    async fn load_record(&self, id: &str) -> Result<Option<StateRecord>, StoreError>;
    async fn upsert_record(&self, record: &StateRecord) -> Result<ApplyResult, StoreError>;
    async fn pending_records(&self, limit: usize) -> Result<Vec<StateRecord>, StoreError>;
    async fn save_peer_cursor(&self, peer: &PeerId, cursor: Cursor) -> Result<(), StoreError>;
}
```

The actual Rust async trait syntax may use `async_trait` or explicit boxed
futures depending on the selected runtime; the wire and merge contracts do not
depend on that choice.

## 8. CRDT model and conflict resolution

### 8.1 State-based LWW register map

The replicated object is a map:

```text
notification_id -> (state, version, tombstone)
```

Each notification entry is an LWW register whose version ordering is:

```text
(HLC, actor PeerId, operationId)
```

Compare HLC first, then lexicographically compare the lowercase actor ID, then
the operation ID. The maximum record wins. This gives a total deterministic
order and makes merge associative, commutative, and idempotent:

```text
merge(a, b) = max(a, b) per notification_id
```

HLC update rules:

1. On a local transition, observe local wall time and set
   `physical = max(wall, last_physical)`.
2. If physical time is unchanged, increment the logical counter.
3. On receiving a record, update local HLC to be greater than both local and
   received clocks before creating future local operations.
4. If clock skew exceeds the configured bound, accept the record for merge but
   mark the peer `clock_suspect`; do not let an unbounded future timestamp
   permanently dominate local edits. Implement this by clamping the physical
   component to `now + max_future_skew` while retaining the original value in
   audit metadata.

### 8.2 User-visible transition rules

Transitions are generated as complete state records, not partial “set a bit”
patches:

- `active -> dismissed`, `active -> archived`, or `active -> snoozed` creates
  a new version.
- `snoozed -> active` is an explicit unsnooze operation and creates a newer
  version.
- `dismissed` and `archived` can be reversed only by an explicit user action.
- A snooze expiry is a local derived event; it must not silently overwrite a
  newer `dismissed` or `archived` version.
- An archived record is not physically deleted until retention and peer
  acknowledgement rules allow compaction.

### 8.3 Concurrent actions

When two peers concurrently edit the same notification and neither has observed
the other, HLC and actor ID provide deterministic convergence. The winner is
the record with the greater total version. The losing record is retained in a
local audit table for diagnostics but is not applied to the active UI state.

This deliberately favors deterministic convergence over semantic guesses such
as “archive always beats dismiss.” If product requirements later demand that
archive be terminal, encode that as a versioned transition policy in the CRDT
and apply it identically on every peer; never implement it in a UI-only layer.

### 8.4 Tombstones and compaction

Tombstones prevent an old out-of-order packet from resurrecting an archived or
deleted state. Keep them until every trusted peer has acknowledged a state
version newer than the tombstone, or until a bounded offline-peer retention
period expires and the user explicitly accepts possible resurrection.

Compaction protocol:

1. exchange per-peer digest and acknowledged maximum version;
2. mark a tombstone compactable only when all active peers dominate it;
3. retain a checkpoint root and checkpoint version;
4. reject deltas older than the checkpoint unless they include a newer state.

## 9. Session protocol

After TLS and application authentication:

```text
A -> B: HELLO(protocol=1, peer_id, capabilities, nonce)
B -> A: HELLO_ACK(protocol=1, peer_id, nonce, session_id)
A -> B: DIGEST(state_root, bucket_roots, max_hlc)
B -> A: DIGEST(...)
A -> B: REQUEST(missing buckets/IDs)
B -> A: DELTA(records[...], cursor)
A -> B: ACK(accepted IDs, rejected IDs, state_root)
... repeat until roots match ...
A <-> B: PING/PONG
```

The initiator and responder both run the same merge code. A session is complete
only when both sides have acknowledged the final digest; otherwise it is
resumable from the saved cursor.

Use time-bucket Merkle roots for efficient anti-entropy. If roots differ, walk
bucket roots before requesting individual IDs. A full digest is a recovery path
for corrupted or missing cursors.

## 10. Out-of-order, duplicate, and lost packet handling

- **Duplicate frame:** identify by `requestId` and record `operationId`; return
  the previous ACK without reapplying side effects.
- **Reordered frame:** validate and merge each record independently; the CRDT
  version max makes arrival order irrelevant.
- **Missing frame:** request the cursor range again after an ACK timeout; the
  sender may resend safely.
- **Stale frame:** return `accepted=false, reason=stale` only for observability;
  it is not a protocol failure because stale records are harmless.
- **Gap in cursor:** do not assume sequence numbers imply causal order; request
  the missing range or restart digest reconciliation.
- **Conflicting same operation ID:** reject and quarantine if the payload hash
  differs from the previously seen operation ID.
- **Malformed/oversized frame:** close the session, increment peer fault score,
  and apply a cooldown. Never attempt to parse partial payloads.
- **Connection close after apply before ACK:** sender retries; receiver returns
  the idempotent ACK from its operation/result table.

## 11. Network error recovery

Classify errors before retrying:

| Error | Action |
|---|---|
| mDNS unavailable | Keep configured peers; retry browse with 1–60 s backoff. |
| DNS/address failure | Retry current addresses, then refresh discovery. |
| TLS certificate mismatch | Do not retry rapidly; mark peer untrusted and require re-pairing. |
| TLS timeout/reset | Reconnect with jittered exponential backoff. |
| `busy` / rate limited | Honor `retry_after_ms`; keep outbox durable. |
| Protocol mismatch | Stop session; show upgrade/re-pair guidance. |
| Invalid signature | Close session, quarantine peer, require explicit review. |
| SQLite busy | Retry boundedly with transaction backoff; never drop the delta. |
| Model/UI unavailable | Continue synchronization; state sync has no model dependency. |

Recommended reconnect schedule:

```text
delay = min(60 seconds, 1 second * 2^attempt) + random(0, 250 ms)
```

Reset the attempt counter after a successful authenticated session. Cap
simultaneous sessions at one per peer and global concurrent sessions at a small
device-specific limit.

The outbox stores records until every trusted peer has acknowledged them or the
local retention policy expires. A peer being offline must not block local state
transitions or other peers.

## 12. Security and privacy controls

- mDNS is discovery only, never authentication.
- Pin peer certificates after explicit pairing.
- Use TLS 1.3 and Ed25519 signatures; protect private keys with platform
  keystores.
- Authenticate before revealing notification IDs or state records.
- Rate-limit handshake attempts and malformed frames per source address.
- Treat local-network peers as hostile until paired; local networks are not a
  trust boundary.
- Log IDs, hashes, error codes, and peer fingerprints, not notification text.
- Support revoke-all-peers and rotate-local-identity operations.
- Keep synchronization disabled by default until the user enables it.

## 13. Acceptance tests

1. Discover two peers, pair them, and reject a third unpaired peer.
2. Verify certificate fingerprint mismatch never reaches CRDT merge.
3. Apply the same delta one, two, and many times; final state is identical.
4. Deliver records in reverse order and verify identical state roots.
5. Create concurrent dismiss/archive/snooze operations and verify every peer
   selects the same `(HLC, actor, operationId)` winner.
6. Kill the process after applying a record but before ACK; reconnect and verify
   no duplicate UI transition.
7. Drop every Nth frame and verify cursor retry plus digest anti-entropy converge.
8. Send stale packets after tombstone compaction eligibility; verify they cannot
   resurrect state.
9. Inject invalid signatures, oversized frames, skewed clocks, and conflicting
   operation IDs; verify quarantine and no state mutation.
10. Run with mDNS blocked and verify configured peers still synchronize.

## 14. Recommended implementation sequence

1. Implement the SQLite CRDT store and pure merge tests.
2. Implement canonical CBOR encoding, frame limits, and signature verification.
3. Implement TLS pairing and trusted-peer persistence.
4. Implement mDNS discovery as an optional connection hint.
5. Implement digest/cursor anti-entropy and durable outbox retries.
6. Bind the Rust core to TypeScript and integrate platform lifecycle events.
7. Add packet-loss, process-kill, clock-skew, and certificate-rotation tests
   before enabling synchronization by default.
