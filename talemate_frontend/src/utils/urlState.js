/**
 * Hash <-> app-state translation. Pure: no Vue, no DOM, no side effects.
 *
 * Grammar (see docs/fork/url-state-design.md):
 *
 *   #/                                            home, no scene
 *   #/templates                                   scene-independent tab
 *   #/prompts?p=llm-templates
 *   #/s/<slug>                                    scene loaded, implies main
 *   #/s/<slug>/main
 *   #/s/<slug>/world/<page>[/<sub1>[/<sub2>]]
 *   #/s/<slug>/mods
 *
 * Overlays ride in the query, because several can be open at once while the
 * path describes exactly one location:
 *
 *   ?config=<tab>[/<page>]  ?debug=<tab>
 *   ?d=scene,settings,debug,director            ?save=<filename>
 *   ?p=<promptsTab>
 *   ?img=<assetId>     a story image open in the full-size asset viewer
 *   ?edit=<assetId>    the image-editing instructions prompt for that image
 *   ?del=1             that edit will discard the old image ("Edit and Delete")
 *
 * The Visual Library rides in one key, because its tab and its selection are one
 * location rather than two independent overlays:
 *
 *   ?visual=review                      open on the Review Queue
 *   ?visual=pending                     open on the Pending Queue
 *   ?visual=scene                       open on Scene Assets, nothing selected
 *   ?visual=<assetId>                   asset selected, Info sub-tab
 *   ?visual=<assetId>/reference         asset + sub-tab
 *   ?visual=<assetId>/cover_crop
 *   ?vlopen=CHARACTER_PORTRAIT,CHARACTER_PORTRAIT::Kaira
 *
 * `review`/`pending`/`scene` are reserved; anything else is an asset id (they are
 * 64-char hex, so nothing can collide). The sub-tab is omitted when it is `info`,
 * which is what keeps a bare `?visual=<assetId>` meaning exactly what it meant
 * before the Visual Library gained its own state.
 *
 * The Review/Pending queue *selections* are deliberately absent: they are indices
 * into arrays that only exist in this browser tab (unsaved generations, queued
 * requests), so after a reload an index resolves to nothing or to a different
 * image. The tab restores; the selection inside it cannot.
 *
 * The path segment for the package-manager tab is `mods`, matching its UI label;
 * the internal tab value is `package_manager`.
 */

// Tab values that TalemateApp understands, mapped to their URL segment.
const TAB_TO_SEGMENT = {
    main: "main",
    world: "world",
    package_manager: "mods",
    templates: "templates",
    prompts: "prompts",
    home: "home",
};

const SEGMENT_TO_TAB = Object.fromEntries(
    Object.entries(TAB_TO_SEGMENT).map(([tab, segment]) => [segment, tab]),
);

// Drawer state keys, in the order they are emitted so the hash is stable.
// The debug drawer is deliberately absent: `?debug=<tab>` already implies it is
// open, and carrying it in both places invites the two disagreeing.
export const DRAWERS = ["scene", "settings", "director"];

// Query keys this module owns. Anything else found in the query is preserved
// verbatim in `extra` and re-emitted, so an unrecognised key from a newer build
// survives a round trip instead of being silently dropped.
const OWNED_QUERY_KEYS = [
    "save",
    "p",
    "visual",
    "vlopen",
    "config",
    "debug",
    "d",
    "img",
    "edit",
    "del",
];

// Reserved `visual` values that name a library tab rather than an asset.
const VISUAL_TABS = ["review", "pending", "scene"];

// Sub-tabs of the asset detail panel (VisualImageView.vue:82-86). `info` is the
// default and is never emitted, so `?visual=<id>` keeps its original meaning.
const VISUAL_DETAIL_TABS = ["info", "reference", "cover_crop"];

export function emptyState() {
    return {
        scene: null,
        save: null,
        tab: "home",
        wsm: [],
        promptsTab: null,
        // Visual Library. `visualTab` is which of its three tabs is open;
        // `visual` is the selected scene asset (which implies the `scene` tab);
        // `visualDetailTab` is the asset panel's sub-tab, null meaning `info`;
        // `vlopen` is the set of expanded tree folders.
        visualTab: null,
        visual: null,
        visualDetailTab: null,
        vlopen: [],
        config: null,
        debug: null,
        // A story image open in the full-size viewer.
        img: null,
        // A story image whose editing-instructions prompt is open, and whether
        // that edit discards the original.
        edit: null,
        editDelete: false,
        drawers: [],
        extra: {},
    };
}

function decode(segment) {
    try {
        return decodeURIComponent(segment);
    } catch {
        // A hand-edited hash can contain a lone `%`, which throws. Prefer the
        // raw text over losing the whole restore.
        return segment;
    }
}

/**
 * Decode one query *value*. Unlike a path segment, `+` means space here (that is
 * what `encodeQueryValue` emits, and what URLSearchParams would decode), while a
 * literal plus arrives as %2B and so is unaffected.
 */
function decodeValue(value) {
    return decode(String(value).replace(/\+/g, "%20"));
}

/**
 * Pull one parameter out of a query string *without* decoding it.
 *
 * Needed for list-valued keys: the delimiter between items is a literal comma we
 * wrote ourselves, while a comma inside an item is still %2C. Decoding before the
 * split would erase that distinction.
 */
function rawParam(query, key) {
    for (const pair of String(query).split("&")) {
        const eq = pair.indexOf("=");
        const k = eq === -1 ? pair : pair.slice(0, eq);
        if (k === key) {
            return eq === -1 ? "" : pair.slice(eq + 1);
        }
    }
    return null;
}

/**
 * Encode one query value, then relax the escapes that are legal unencoded in a
 * fragment and that we never use as delimiters. This is what keeps the hash
 * readable without the correctness hole the old blanket un-escaping had:
 * `,` and `/` stay encoded, so they can never be mistaken for our delimiters.
 */
function encodeQueryValue(value) {
    return encodeURIComponent(String(value))
        .replace(/%20/g, "+") // spaces; a literal plus is %2B, so this is reversible
        .replace(/%3A/g, ":"); // colons, as in CHARACTER_PORTRAIT::Kaira
}

/**
 * @param {string} hash - `location.hash`, with or without the leading `#`.
 * @returns {object} state
 */
export function parse(hash) {
    const state = emptyState();
    if (!hash) {
        return state;
    }

    let raw = hash.startsWith("#") ? hash.slice(1) : hash;

    // Split query off the path.
    const queryIndex = raw.indexOf("?");
    let query = "";
    if (queryIndex !== -1) {
        query = raw.slice(queryIndex + 1);
        raw = raw.slice(0, queryIndex);
    }

    const segments = raw.split("/").filter((s) => s.length > 0);

    let cursor = 0;
    if (segments[cursor] === "s") {
        cursor++;
        if (segments[cursor]) {
            state.scene = decode(segments[cursor]);
            cursor++;
            // A scene in the hash with no tab segment means the main view.
            state.tab = "main";
        }
    }

    const tabSegment = segments[cursor];
    if (tabSegment && SEGMENT_TO_TAB[tabSegment]) {
        state.tab = SEGMENT_TO_TAB[tabSegment];
        cursor++;
        if (state.tab === "world") {
            // page, sub1, sub2 — e.g. characters/Kaira/attributes
            state.wsm = segments.slice(cursor, cursor + 3).map(decode);
        }
    }

    if (query) {
        const params = new URLSearchParams(query);
        state.save = params.get("save");
        state.promptsTab = params.get("p");
        const visual = params.get("visual");
        if (visual) {
            const [head, sub] = visual.split("/");
            if (VISUAL_TABS.includes(head)) {
                state.visualTab = head;
            } else if (head) {
                // Not a reserved token, so it is an asset id — which lives on the
                // scene tab by definition.
                state.visual = head;
                state.visualTab = "scene";
                if (sub && VISUAL_DETAIL_TABS.includes(sub) && sub !== "info") {
                    state.visualDetailTab = sub;
                }
            }
        }

        // Read from the raw query, NOT via URLSearchParams: `get()` decodes the
        // whole value, which would turn a %2C inside a folder name into a real
        // comma before the split and tear one folder id into two.
        const vlopenRaw = rawParam(query, "vlopen");
        if (vlopenRaw) {
            state.vlopen = vlopenRaw
                .split(",")
                .map((part) => decodeValue(part.trim()))
                .filter(Boolean);
        }

        state.config = params.get("config");
        state.debug = params.get("debug");
        state.img = params.get("img");
        state.edit = params.get("edit");
        state.editDelete = params.get("del") === "1";

        const drawers = params.get("d");
        if (drawers) {
            state.drawers = drawers
                .split(",")
                .map((d) => d.trim())
                .filter((d) => DRAWERS.includes(d));
        }

        for (const [key, value] of params.entries()) {
            if (!OWNED_QUERY_KEYS.includes(key)) {
                state.extra[key] = value;
            }
        }
    }

    return state;
}

/**
 * @param {object} state
 * @returns {string} hash including the leading `#`
 */
export function stringify(state) {
    const s = { ...emptyState(), ...(state || {}) };

    const path = [];
    if (s.scene) {
        path.push("s", encodeURIComponent(s.scene));
    }

    const segment = TAB_TO_SEGMENT[s.tab];
    // `main` is implied by a bare `#/s/<slug>`, but emit it anyway: an explicit
    // segment makes back/forward entries distinguishable and reads better in a
    // bug report. `home` is emitted too whenever a scene is loaded — a scene can
    // be loaded while the user sits on the home tab, and dropping the segment
    // there would round-trip that state back as `main`. With no scene, home is
    // simply `#/`.
    if (segment && !(s.tab === "home" && !s.scene)) {
        path.push(segment);
    }

    if (s.tab === "world" && Array.isArray(s.wsm)) {
        for (const part of s.wsm) {
            if (part === null || part === undefined || part === "") {
                break; // never emit a hole — a trailing gap would shift sub2 into sub1
            }
            path.push(encodeURIComponent(part));
        }
    }

    // Assembled by hand rather than with URLSearchParams. Its `toString()`
    // percent-encodes `,`, so the old code un-escaped every %2C in the finished
    // query to keep the hash readable — which also un-escaped commas that were
    // *inside* a value. Folder ids embed character names, so `Smith, John` would
    // have been torn into two folder ids on the way back. Here each value is
    // encoded individually and only the delimiters we insert are literal.
    const pairs = [];
    const push = (key, value) => pairs.push(`${key}=${encodeQueryValue(value)}`);

    if (s.save) push("save", s.save);
    if (s.promptsTab) push("p", s.promptsTab);

    // Visual Library: an asset id wins over the bare tab, since it is the more
    // specific description of the same location.
    if (s.visual) {
        const detail =
            s.visualDetailTab && s.visualDetailTab !== "info" ? `/${s.visualDetailTab}` : "";
        // The id is hex and the sub-tab a known token, so the `/` between them is
        // safe to leave literal.
        pairs.push(`visual=${encodeQueryValue(s.visual)}${detail}`);
    } else if (s.visualTab) {
        push("visual", s.visualTab);
    }

    if (Array.isArray(s.vlopen) && s.vlopen.length) {
        pairs.push(`vlopen=${s.vlopen.map(encodeQueryValue).join(",")}`);
    }

    if (s.config) {
        // `<tab>/<page>`, both known tokens — keep the separator literal so the
        // value stays readable, encoding each half.
        const parts = String(s.config).split("/").map(encodeQueryValue);
        pairs.push(`config=${parts.join("/")}`);
    }
    if (s.debug) push("debug", s.debug);
    if (s.img) push("img", s.img);
    if (s.edit) {
        push("edit", s.edit);
        // Only meaningful alongside `edit`, so never emitted on its own.
        if (s.editDelete) push("del", "1");
    }
    if (Array.isArray(s.drawers) && s.drawers.length) {
        const ordered = DRAWERS.filter((d) => s.drawers.includes(d));
        if (ordered.length) pairs.push(`d=${ordered.join(",")}`);
    }
    for (const [key, value] of Object.entries(s.extra || {})) {
        if (!OWNED_QUERY_KEYS.includes(key)) push(key, value);
    }

    const query = pairs.join("&");
    return `#/${path.join("/")}${query ? `?${query}` : ""}`;
}

/**
 * True when two states describe the same place. Used to suppress no-op writes
 * and to detect a deep link that already matches the live app (AC7).
 */
export function sameState(a, b) {
    return stringify(a) === stringify(b);
}
