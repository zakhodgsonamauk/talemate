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
 *   ?visual=<assetId>  ?config=<tab>[/<page>]  ?debug=<tab>
 *   ?d=scene,settings,debug,director            ?save=<filename>
 *   ?p=<promptsTab>
 *   ?img=<assetId>     a story image open in the full-size asset viewer
 *   ?edit=<assetId>    the image-editing instructions prompt for that image
 *   ?del=1             that edit will discard the old image ("Edit and Delete")
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
const OWNED_QUERY_KEYS = ["save", "p", "visual", "config", "debug", "d", "img", "edit", "del"];

export function emptyState() {
    return {
        scene: null,
        save: null,
        tab: "home",
        wsm: [],
        promptsTab: null,
        visual: null,
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
        state.visual = params.get("visual");
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

    const params = new URLSearchParams();
    if (s.save) params.set("save", s.save);
    if (s.promptsTab) params.set("p", s.promptsTab);
    if (s.visual) params.set("visual", s.visual);
    if (s.config) params.set("config", s.config);
    if (s.debug) params.set("debug", s.debug);
    if (s.img) params.set("img", s.img);
    if (s.edit) {
        params.set("edit", s.edit);
        // Only meaningful alongside `edit`, so never emitted on its own.
        if (s.editDelete) params.set("del", "1");
    }
    if (Array.isArray(s.drawers) && s.drawers.length) {
        const ordered = DRAWERS.filter((d) => s.drawers.includes(d));
        if (ordered.length) params.set("d", ordered.join(","));
    }
    for (const [key, value] of Object.entries(s.extra || {})) {
        if (!OWNED_QUERY_KEYS.includes(key)) params.set(key, value);
    }

    // URLSearchParams percent-encodes `,` and `/`. Both are legal unescaped in a
    // fragment, and the whole point of this feature is a hash a human can read,
    // so put them back. Nothing else is touched.
    const query = params.toString().replace(/%2C/g, ",").replace(/%2F/g, "/");
    return `#/${path.join("/")}${query ? `?${query}` : ""}`;
}

/**
 * True when two states describe the same place. Used to suppress no-op writes
 * and to detect a deep link that already matches the live app (AC7).
 */
export function sameState(a, b) {
    return stringify(a) === stringify(b);
}
