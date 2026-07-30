/**
 * The bridge between TalemateApp's component state and the flat state object in
 * `urlState.js`.
 *
 * Everything that knows *where a piece of navigation state lives* belongs here,
 * so adding a new URL-addressable modal is an edit to this file rather than a new
 * diff in TalemateApp.vue. That matters: TalemateApp.vue is the single hottest
 * upstream file in the fork (see FORK.md).
 *
 * Deep `$refs` chains below are deliberate and match how the surrounding code
 * already reaches across components (TalemateApp.vue:822-825,
 * WorldStateManager.vue:312-323).
 */

import { emptyState, DRAWERS } from "@/utils/urlState";

/**
 * Read the world-editor path out of WorldStateManager.
 * Returns [] when nothing meaningful is selected.
 */
function readWorldEditorPath(app) {
    const wsm = app.$refs.worldStateManager;
    if (!wsm || !wsm.tab) {
        return [];
    }

    const page = wsm.tab;

    if (page === "characters") {
        const characters = wsm.$refs && wsm.$refs.characters;
        const name = characters && characters.selected;
        if (!name) {
            return [page];
        }
        const sub = characters.page;
        return sub ? [page, name, sub] : [page, name];
    }

    return [page];
}

/**
 * Snapshot the app's navigation state as a plain object.
 */
export function readState(app) {
    const state = emptyState();

    if (app.sceneActive && app.scene && app.scene.data) {
        // Always the payload's own slug — `Scene.project_name` has a setter
        // (tale_mate.py:317) that can override the derived value, so deriving it
        // client-side from the title would be wrong for those scenes.
        state.scene = app.scene.data.project_name || null;

        // Only disambiguate the save file when there is genuine ambiguity.
        const saveFiles = app.scene.data.save_files || [];
        if (state.scene && saveFiles.length > 1 && app.scene.data.filename) {
            state.save = app.scene.data.filename;
        }
    }

    state.tab = app.tab || "home";

    if (state.tab === "world") {
        state.wsm = readWorldEditorPath(app);
    }

    if (state.tab === "prompts" && app.promptsMainTab && app.promptsMainTab !== "prompts") {
        state.promptsTab = app.promptsMainTab;
    }

    const visualLibrary = app.$refs.visualLibrary;
    if (visualLibrary && visualLibrary.dialog && visualLibrary.sceneSelectedId) {
        state.visual = visualLibrary.sceneSelectedId;
    }

    const appConfig = app.$refs.appConfig;
    if (appConfig && appConfig.dialog) {
        state.config = appConfig.tab || "game";
    }

    // Story images: the full-size viewer, and the editing-instructions prompt.
    // Both live in SceneMessages, so both are only present on the main tab.
    const sceneMessages = app.$refs.sceneMessages;
    if (sceneMessages) {
        if (sceneMessages.viewedAssetId) {
            state.img = sceneMessages.viewedAssetId;
        }

        const editPrompt = sceneMessages.$refs && sceneMessages.$refs.requestRegenerateInstructions;
        if (editPrompt && editPrompt.open) {
            const params = editPrompt.extra_params || {};
            if (params.asset_id) {
                state.edit = params.asset_id;
                state.editDelete = !!params.deleteOld;
            }
        }
    }

    if (app.debugDrawer) {
        const debugTools = app.$refs.debugTools;
        // Fall back to the bare key so the drawer's open state still survives a
        // refresh even if the tab can't be read.
        state.debug = (debugTools && debugTools.tab) || "prompts";
    }

    if (app.sceneDrawer) state.drawers.push("scene");
    if (app.drawer) state.drawers.push("settings");
    if (app.directorConsoleDrawer) state.drawers.push("director");

    return state;
}

/**
 * Apply the story-image overlays: the full-size viewer (`?img=`) and the
 * editing-instructions prompt (`?edit=`).
 *
 * Both are no-ops unless SceneMessages is mounted, which is the honest outcome —
 * they belong to the story view and cannot exist without it.
 */
async function applyStoryImageState(app, state) {
    const sceneMessages = app.$refs.sceneMessages;
    if (!sceneMessages) {
        return;
    }

    // The viewer is pure client state, so it can be set directly. An id naming an
    // asset that is not in the message list simply shows nothing, so only accept
    // ids the loaded messages actually reference.
    const messages = sceneMessages.messages || [];
    const known = (assetId) => messages.some((m) => m.asset_id === assetId);

    sceneMessages.viewedAssetId = state.img && known(state.img) ? state.img : null;

    const editPrompt = sceneMessages.$refs && sceneMessages.$refs.requestRegenerateInstructions;
    if (!editPrompt) {
        return;
    }

    if (state.edit) {
        // `handleRegenerateAssetWithInstructions` needs the message id, which the
        // hash does not carry — recover it from the message that owns the asset.
        // Keeping message ids out of the URL means the link stays valid across
        // edits that renumber messages.
        const owner = messages.find((m) => m.asset_id === state.edit);
        const alreadyOpen = editPrompt.open && (editPrompt.extra_params || {}).asset_id === state.edit;
        if (owner && !alreadyOpen) {
            editPrompt.openDialog({
                asset_id: state.edit,
                message_id: owner.id,
                deleteOld: !!state.editDelete,
            });
        }
    } else if (editPrompt.open) {
        // Closing via `open` rather than `cancel()` on purpose: cancel emits an
        // event that callers may act on, and a back-press is not a user cancelling
        // the operation, it is navigation.
        editPrompt.open = false;
    }
}

/**
 * Push a state object into the app.
 *
 * Ordering is not cosmetic. The `availableTabs` watcher
 * (TalemateApp.vue:592-601) force-resets `tab` to `home` whenever the target
 * tab's `condition()` is false, and `main`/`world`/`package_manager` all gate on
 * `sceneActive`. So: set the tab only if it is currently available, then let Vue
 * flush before touching anything that lives inside that tab.
 *
 * @returns {Promise<void>}
 */
export async function applyState(app, state) {
    const wanted = state.tab || "home";
    const available = (app.availableTabs || []).some((t) => t.value === wanted);
    app.tab = available ? wanted : "home";

    await app.$nextTick();

    if (app.tab === "world" && state.wsm && state.wsm.length) {
        const wsm = app.$refs.worldStateManager;
        if (wsm && typeof wsm.show === "function") {
            wsm.show(state.wsm[0], state.wsm[1], state.wsm[2]);
        }
    }

    if (app.tab === "prompts" && state.promptsTab) {
        app.promptsMainTab = state.promptsTab;
    }

    const drawers = state.drawers || [];
    app.sceneDrawer = drawers.includes("scene");
    app.drawer = drawers.includes("settings");
    app.directorConsoleDrawer = drawers.includes("director");
    app.debugDrawer = !!state.debug;

    await app.$nextTick();

    if (state.debug) {
        const debugTools = app.$refs.debugTools;
        if (debugTools && typeof debugTools.selectTab === "function") {
            debugTools.selectTab(state.debug);
        }
    }

    const appConfig = app.$refs.appConfig;
    if (state.config) {
        const [configTab, configPage] = String(state.config).split("/");
        app.openAppConfig(configTab, configPage);
    } else if (appConfig && appConfig.dialog) {
        appConfig.dialog = false;
    }

    await applyStoryImageState(app, state);

    const visualLibrary = app.$refs.visualLibrary;
    if (state.visual) {
        if (visualLibrary && typeof visualLibrary.openWithAsset === "function") {
            visualLibrary.openWithAsset(state.visual);
        }
    } else if (visualLibrary && visualLibrary.dialog) {
        // Through `dialogModel`, not `dialog` — the setter runs the
        // unsaved-changes confirmation (VisualLibrary.vue:353-372). Assigning
        // `dialog` directly would discard pending edits with no prompt.
        visualLibrary.dialogModel = false;
    }
}

export { DRAWERS };
