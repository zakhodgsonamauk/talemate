/**
 * Keeps `location.hash` and TalemateApp's navigation state in sync.
 *
 * Two directions, both guarded so they cannot feed each other:
 *
 *   write  state -> hash    debounced; suppressed while restoring
 *   read   hash  -> state   on `hashchange` (back/forward), ignoring our own writes
 *
 * The boot hash is captured in `created()`, before the websocket connects. This is
 * not premature: `websocket.onclose` sets `sceneActive = false` and `scene = {}`
 * (TalemateApp.vue:954-955), which trips the `availableTabs` watcher, which resets
 * `tab` to `home`, which fires the writer. If restore read `location.hash` lazily
 * it would find `#/` — its own writer's output — instead of what the user loaded.
 *
 * Naming is `url*` / `_url*` throughout to stay clear of TalemateApp's own members.
 */

import { parse, stringify, emptyState } from "@/utils/urlState";
import { readState, applyState } from "@/utils/urlStateSlices";

/**
 * A scene's save file lives at `<scenes_dir>/<project_name>/<filename>`
 * (tale_mate.py:354-368), so the slug is simply the parent directory name. That
 * makes slug -> path resolution a client-side string operation against data the
 * frontend already has, and needs no backend change.
 */
function slugFromPath(path) {
    const parts = String(path || "").replace(/\\/g, "/").split("/").filter(Boolean);
    return parts.length >= 2 ? parts[parts.length - 2] : null;
}

function filenameFromPath(path) {
    const parts = String(path || "").replace(/\\/g, "/").split("/").filter(Boolean);
    return parts.length ? parts[parts.length - 1] : null;
}

/**
 * How often the live app state is compared against the hash.
 *
 * This is a poll rather than a reactive watcher, and that is deliberate. The state
 * being mirrored lives inside child components reached through `$refs` — the
 * world-editor page, the selected character, the open modal. `$refs` is not
 * reactive and is empty until the child renders, so a computed that reads it
 * registers no dependency on the child's data and then never re-runs. Tabs are
 * rendered lazily here, so that is the normal case, not an edge case.
 *
 * The check is a handful of property reads plus a string compare, and it exits
 * early when the hash already matches, so running it a few times a second costs
 * nothing measurable and removes an entire class of "the URL silently stopped
 * updating" bugs.
 */
const SYNC_INTERVAL_MS = 300;

/**
 * True when the only difference between two states is which drawers are open.
 * Drawer toggles are incidental — they get replaceState so they don't clutter
 * the back stack.
 */
function onlyDrawersDiffer(a, b) {
    return stringify({ ...a, drawers: [] }) === stringify({ ...b, drawers: [] });
}

export default {
    data() {
        return {
            // Banner shown after an auto-load. null when nothing to report.
            // { text, level } where level is 'info' | 'warning' | 'error'.
            urlRestoreNotice: null,
        };
    },
    created() {
        // Snapshot before anything can overwrite it. See the note above.
        this._urlBootHash = window.location.hash || "";
        this._urlBootState = parse(this._urlBootHash);

        this._urlLastWritten = null;
        this._urlLastState = null;
        this._urlRestoring = false;
        // The writer stays disarmed until the boot hash has been consumed, so it
        // cannot clobber the URL we are about to restore from.
        this._urlWriterArmed = false;
        // Auto-load happens at most once per page load. Later reconnects (backend
        // restart, dropped socket) restore view state but must not re-issue
        // load_scene, or a flapping backend would loop.
        this._urlAutoLoadDone = false;
        // Set once the boot hash has been dealt with, successfully or not.
        this._urlBootConsumed = false;
        // Slug we are waiting on a `scenes_list` reply for, if any.
        this._urlPendingLookup = null;
        this._urlSyncTimer = null;
        this._urlLoadTimer = null;
        this._urlHashChangeHandler = null;
    },
    mounted() {
        this._urlHashChangeHandler = () => this.onUrlHashChange();
        window.addEventListener("hashchange", this._urlHashChangeHandler);

        // Registered rather than bolted into handleMessage — this is the
        // documented extension point (TalemateApp.vue:972-983) and costs the
        // upstream message dispatcher no diff at all.
        this.registerMessageHandler(this.urlOnMessage);

        this._urlSyncTimer = setInterval(() => this.writeUrlState(), SYNC_INTERVAL_MS);
    },
    beforeUnmount() {
        if (this._urlHashChangeHandler) {
            window.removeEventListener("hashchange", this._urlHashChangeHandler);
            this._urlHashChangeHandler = null;
        }
        this.unregisterMessageHandler(this.urlOnMessage);
        clearInterval(this._urlSyncTimer);
        clearTimeout(this._urlLoadTimer);
    },
    methods: {
        /** The state the page was opened with. */
        urlBootState() {
            return this._urlBootState || emptyState();
        },

        urlArmWriter() {
            this._urlWriterArmed = true;
        },

        writeUrlState() {
            if (!this._urlWriterArmed || this._urlRestoring) {
                return;
            }

            let next, hash;
            try {
                next = readState(this);
                hash = stringify(next);
            } catch (e) {
                // A broken read must never take the app down with it.
                console.error("urlState: write failed", e);
                return;
            }

            if (hash === (window.location.hash || "#/")) {
                return;
            }

            // Push or replace is decided from what actually changed, not by the
            // caller. Somewhere-else (tab, world-editor page, a modal) earns a
            // history entry so `back` undoes it. A drawer toggle does not — it
            // would otherwise take several back-presses to escape a sidebar.
            const incidental = this._urlLastState && onlyDrawersDiffer(this._urlLastState, next);

            this._urlLastWritten = hash;
            this._urlLastState = next;

            try {
                if (incidental) {
                    window.history.replaceState(window.history.state, "", hash);
                } else {
                    window.history.pushState(window.history.state, "", hash);
                }
            } catch {
                // Assignment always works, at the cost of a forced history entry.
                window.location.hash = hash;
            }
        },

        /**
         * Back/forward, or a hand-edited hash. Our own writes are filtered out by
         * comparing against `_urlLastWritten`.
         */
        async onUrlHashChange() {
            const hash = window.location.hash || "";

            if (hash === this._urlLastWritten) {
                return;
            }

            const target = parse(hash);

            // A scene change via the hash alone is not honoured here. Loading a
            // scene tears down the previous one server-side
            // (websocket_server.py:155-166), which is far too destructive to
            // trigger from a back-button press. View state only.
            this._urlRestoring = true;
            try {
                await applyState(this, target);
            } catch (e) {
                console.error("urlState: apply failed", e);
            } finally {
                this._urlRestoring = false;
                // Baseline the writer against what we just applied, so its next
                // run diffs against reality rather than pre-navigation state.
                this._urlLastWritten = hash;
                this._urlLastState = readState(this);
            }
        },

        // ---------------------------------------------------------------
        // Boot restore
        // ---------------------------------------------------------------

        urlOnMessage(data) {
            if (!data || !data.type) {
                return;
            }

            if (data.type === "app_config") {
                // Handlers run before TalemateApp assigns `this.appConfig`
                // (TalemateApp.vue:983 vs :1104), so wait a tick for the config —
                // and therefore recent_scenes — to actually be there.
                this.$nextTick(() => this.urlMaybeAutoLoadScene());
                return;
            }

            if (data.type === "scene_status") {
                this.$nextTick(() => this.urlOnSceneStatus());
                return;
            }

            if (data.type === "scenes_list" && this._urlPendingLookup) {
                this.urlOnScenesList(data.data || []);
            }
        },

        /**
         * Resolve a slug against the recent-scenes list the backend already ships
         * in app_config (config/schema.py:682).
         */
        urlResolveFromRecents(slug, save) {
            const scenes =
                (this.appConfig && this.appConfig.recent_scenes && this.appConfig.recent_scenes.scenes) || [];
            const bySlug = scenes.filter((entry) => slugFromPath(entry.path) === slug);
            if (!bySlug.length) {
                return null;
            }
            if (save) {
                const exact = bySlug.find((entry) => filenameFromPath(entry.path) === save);
                if (exact) {
                    return exact;
                }
            }
            return bySlug[0];
        },

        urlSendLoadScene(path) {
            if (!this.websocket) {
                return;
            }
            this.websocket.send(JSON.stringify({ type: "load_scene", file_path: path }));

            // If the load never lands — a failed load, a scene that errors during
            // init — the writer would stay disarmed and the hash would freeze at
            // whatever the user typed. Give up after a while and hand control back.
            clearTimeout(this._urlLoadTimer);
            this._urlLoadTimer = setTimeout(() => {
                if (this._urlBootConsumed) {
                    return;
                }
                this._urlBootConsumed = true;
                this.urlRestoreNotice = {
                    level: "error",
                    text: "Timed out restoring the scene from the URL.",
                };
                this.urlArmWriter();
            }, 60000);
        },

        /**
         * Decide whether to auto-load the scene named in the boot hash.
         *
         * Runs at most once per page load. A backend restart mid-session
         * reconnects and re-sends app_config, but must not re-issue load_scene —
         * otherwise a flapping backend loops on scene loads (AC5).
         */
        urlMaybeAutoLoadScene() {
            if (this._urlAutoLoadDone) {
                // Reconnect: restore view state only, never touch the scene.
                if (this._urlBootConsumed) {
                    this.urlArmWriter();
                }
                return;
            }
            this._urlAutoLoadDone = true;

            const boot = this.urlBootState();

            if (!boot.scene) {
                this._urlBootConsumed = true;
                this.restoreUrlViewState(boot);
                return;
            }

            // Already the right scene (e.g. HMR, or a hash that matches reality):
            // let the scene_status path finish the restore. No second load (AC7).
            if (this.sceneActive && this.scene && this.scene.data && this.scene.data.project_name === boot.scene) {
                return;
            }

            const entry = this.urlResolveFromRecents(boot.scene, boot.save);
            if (entry) {
                this.urlSendLoadScene(entry.path);
                return;
            }

            // Not in recents doesn't mean it doesn't exist — ask the backend to
            // search the scenes directory before declaring it missing.
            this._urlPendingLookup = boot.scene;
            this.websocket.send(
                JSON.stringify({ type: "request_scenes_list", query: boot.scene, list_images: false }),
            );
        },

        urlOnScenesList(list) {
            const slug = this._urlPendingLookup;
            this._urlPendingLookup = null;
            if (!slug) {
                return;
            }

            const boot = this.urlBootState();
            const candidates = list.filter((entry) => slugFromPath(entry.path) === slug);
            const match =
                (boot.save && candidates.find((entry) => filenameFromPath(entry.path) === boot.save)) ||
                candidates[0];

            if (!match) {
                // AC6: say so, land on home, and stop. No retry loop.
                this.urlRestoreNotice = {
                    level: "error",
                    text: `No scene named "${slug}" was found — showing the home screen instead.`,
                };
                this._urlBootConsumed = true;
                this.restoreUrlViewState({ ...emptyState(), tab: "home" });
                return;
            }

            this.urlSendLoadScene(match.path);
        },

        /**
         * A scene landed. If it's the one the URL asked for, finish the restore.
         */
        urlOnSceneStatus() {
            if (this._urlBootConsumed) {
                return;
            }

            const boot = this.urlBootState();
            if (!boot.scene) {
                return;
            }

            const slug = this.scene && this.scene.data && this.scene.data.project_name;
            if (!slug) {
                return;
            }

            this._urlBootConsumed = true;

            if (slug !== boot.scene) {
                // Something else got loaded first — most likely the user clicked a
                // scene while we were still resolving. Their action wins; abandon
                // the boot target and let the writer take over.
                this.urlArmWriter();
                return;
            }

            const title = (this.scene && (this.scene.title || this.scene.name)) || boot.scene;
            // Deliberately no timestamp. The only date available here is
            // `recent_scenes[].date`, which `RecentScenes.push()` stamps with the
            // current time on every load (config/schema.py:505-507) — it is a
            // last-opened marker, not a save time. Nothing in the payload records
            // when the scene was actually saved, so claiming one would be a
            // fabrication.
            const autoSaveOff =
                this.appConfig &&
                this.appConfig.game &&
                this.appConfig.game.general &&
                this.appConfig.game.general.auto_save === false;

            if (autoSaveOff) {
                this.urlRestoreNotice = {
                    level: "warning",
                    text:
                        `Reloaded ${title} from disk. Auto-save is off, so anything ` +
                        "after the last manual save is not here.",
                };
            } else {
                this.urlRestoreNotice = {
                    level: "info",
                    text: `Reloaded ${title} from disk.`,
                };
            }

            this.restoreUrlViewState(boot);
        },

        /**
         * Apply the non-scene parts of the boot state. Safe to call more than
         * once — used both when there is no scene to load and after one lands.
         */
        async restoreUrlViewState(state) {
            this._urlRestoring = true;
            try {
                await applyState(this, state || this.urlBootState());
            } catch (e) {
                console.error("urlState: restore failed", e);
            } finally {
                this._urlRestoring = false;
                this._urlLastState = readState(this);
                this._urlLastWritten = stringify(this._urlLastState);
                this.urlArmWriter();
            }
        },
    },
};
