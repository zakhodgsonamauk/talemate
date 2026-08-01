<template>
  <v-dialog v-model="internalModel" max-width="720">
    <v-card>
      <v-toolbar density="comfortable" color="grey-darken-4">
        <v-toolbar-title>
          <v-icon class="mr-2" size="small" color="primary">mdi-image-plus</v-icon>
          Generate Image
        </v-toolbar-title>
      </v-toolbar>
      <v-divider></v-divider>
      <v-card-text>
        <v-tabs v-model="mode" density="compact" class="mb-4" color="primary">
          <v-tab value="prompt">Prompt</v-tab>
          <v-tab value="instruct" :disabled="isIterateMode">Instruct</v-tab>
        </v-tabs>

        <v-alert
          v-if="promptFailed"
          type="warning"
          density="compact"
          variant="tonal"
          class="mb-3"
        >
          The agent did not return a composed prompt. Write one yourself below, or cancel.
        </v-alert>

        <v-window v-model="mode">
          <!-- Prompt Mode -->
          <v-window-item value="prompt">
            <v-textarea
              v-model="prompt"
              label="Prompt"
              rows="4"
              auto-grow
              :loading="promptLoading"
              :disabled="generating || promptLoading"
              :placeholder="promptLoading ? 'Composing prompt…' : undefined"
              :persistent-placeholder="promptLoading"
              @keydown.enter.exact.prevent="onSubmit"
            />

            <v-textarea
              v-model="negativePrompt"
              class="mt-2"
              label="Negative Prompt"
              rows="2"
              auto-grow
              :loading="promptLoading"
              :disabled="generating || promptLoading"
            />

            <v-row class="mt-2">
              <v-col cols="6">
                <v-select
                  v-model="visType"
                  :items="visTypeOptions"
                  label="Vis Type"
                  :disabled="generating || promptLoading"
                />
              </v-col>
              <v-col cols="6">
                <v-select
                  v-model="format"
                  :items="formatOptions"
                  label="Format"
                  :disabled="generating || promptLoading"
                />
              </v-col>
            </v-row>

            <v-select
              v-if="isCharacterVisType"
              v-model="characterName"
              class="mt-2"
              :items="characterItems"
              label="Character"
              :disabled="generating || promptLoading || characterItems.length === 0"
            />

            <!-- Checkpoint choice. Only offered when the image backend answered the
                 checkpoints query with actual models (ComfyUI). Choosing one sets
                 the agent's model for every generation path - both tabs here, the
                 plain Visualize chip, character cards, automatic generations - and
                 the sampler settings travel with it on the backend, so a Lightning
                 model is not run at a base model's cfg. -->
            <v-select
              v-if="checkpointChoices.length > 1"
              :model-value="checkpoint"
              class="mt-2"
              :items="checkpointChoices"
              item-title="label"
              item-value="value"
              label="Image Model"
              hint="Applies to all image generation until changed. Sampler settings adjust automatically."
              persistent-hint
              :disabled="generating || promptLoading"
              @update:model-value="onCheckpointChosen"
            />

            <v-alert
              v-if="recomposing"
              type="info"
              color="primary"
              density="compact"
              variant="tonal"
              class="mb-2 mt-2"
            >
              Recomposing the prompt for the selected model's dialect…
            </v-alert>
            <v-alert
              v-if="inlineReference"
              type="info"
              color="primary"
              density="compact"
              variant="tonal"
              class="mb-2 mt-2"
            >
              Iterating on an unsaved image. An inline reference is pre-applied and cannot be removed.
            </v-alert>
            <VisualReferenceImages
              class="mt-2"
              title="Reference Images"
              :reference-assets="referenceAssets"
              :inline-reference="inlineReference"
              :editable="editAvailable && !promptLoading"
              :max-references="maxReferences"
              :available-asset-ids="availableAssetIds"
              :available-assets-map="availableAssetsMap"
              @update:reference-assets="(v) => referenceAssets = v"
            />
            <VisualReferenceImages
              class="mt-2"
              title="Background Reference"
              :reference-assets="backgroundReferenceAssets"
              :editable="editAvailable && !promptLoading"
              :max-references="1"
              :available-asset-ids="availableAssetIds"
              :available-assets-map="availableAssetsMap"
              @update:reference-assets="(v) => backgroundReferenceAssets = v"
            />
            <div class="text-medium-emphasis text-caption mb-2">
              Style transfer only — carries mood, palette and setting from the
              image, never a person's identity.
            </div>
            <v-alert v-if="!editAvailable" type="warning" density="compact" variant="text" class="mt-1 text-caption">
              <div class="text-muted">
                Image references are unavailable. Configure the image edit backend in the visual agent or check its connection.
              </div>
            </v-alert>
          </v-window-item>

          <!-- Instruct Mode -->
          <v-window-item value="instruct">
            <v-tooltip v-if="currentArtStyle" text="Change art style" location="top">
              <template v-slot:activator="{ props: tooltipProps }">
                <v-menu>
                  <template v-slot:activator="{ props: menuProps }">
                    <v-chip
                      v-bind="Object.assign({}, menuProps, tooltipProps)"
                      size="small"
                      :color="currentArtStyleSource === 'scene' ? 'primary' : 'default'"
                      label
                      clickable
                      class="mb-2"
                      :disabled="generating"
                    >
                      <v-icon start>mdi-palette</v-icon>
                      {{ currentArtStyle }}
                      <span class="text-caption ml-1" style="opacity: 0.7;">
                        ({{ currentArtStyleSource === 'scene' ? 'Scene' : 'Agent' }})
                      </span>
                      <v-icon end>mdi-chevron-down</v-icon>
                    </v-chip>
                  </template>
                  <v-list density="compact">
                    <v-list-item
                      v-for="template in visualStyleTemplates"
                      :key="template.value"
                      @click="updateArtStyle(template.value)"
                      :active="sceneVisualStyleTemplate === template.value"
                    >
                      <template v-slot:prepend>
                        <v-icon>{{ template.value ? 'mdi-palette' : 'mdi-cancel' }}</v-icon>
                      </template>
                      <v-list-item-title>{{ template.title }}</v-list-item-title>
                      <v-list-item-subtitle v-if="template.props?.subtitle">{{ template.props.subtitle }}</v-list-item-subtitle>
                    </v-list-item>
                  </v-list>
                </v-menu>
              </template>
            </v-tooltip>
            <v-textarea
              v-model="instructions"
              label="Instructions"
              hint="Enter instructions for the visual agent to follow."
              rows="4"
              auto-grow
              :disabled="generating"
              @keydown.enter.exact.prevent="onSubmit"
            />

            <v-row class="mt-2">
              <v-col cols="12">
                <v-select
                  v-model="visType"
                  :items="visTypeOptions"
                  label="Vis Type"
                  :disabled="generating"
                />
              </v-col>
            </v-row>

            <v-select
              v-if="isCharacterVisType"
              v-model="characterName"
              class="mt-2"
              :items="characterItems"
              label="Character"
              :disabled="generating || characterItems.length === 0"
            />

            <v-select
              v-if="checkpointChoices.length > 1"
              :model-value="checkpoint"
              class="mt-2"
              :items="checkpointChoices"
              item-title="label"
              item-value="value"
              label="Image Model"
              hint="Applies to all image generation until changed. Sampler settings adjust automatically."
              persistent-hint
              :disabled="generating"
              @update:model-value="onCheckpointChosen"
            />
          </v-window-item>
        </v-window>
      </v-card-text>
      <v-card-actions>
        <v-btn variant="text" @click="close" color="cancel" prepend-icon="mdi-cancel">Cancel</v-btn>
        <v-spacer></v-spacer>
        <v-btn color="primary" variant="text" :disabled="!canSubmit" :loading="generating" @click="onSubmit" prepend-icon="mdi-play">Generate</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script>
import { VIS_TYPE, VIS_TYPE_OPTIONS, FORMAT_OPTIONS, FORMAT_TYPE, GEN_TYPE } from '@/constants/visual';
import VisualReferenceImages from './VisualReferenceImages.vue';
export default {
  name: 'VisualLibraryGenerate',
  inject: ['getWebsocket', 'registerMessageHandler', 'unregisterMessageHandler'],
  components: { VisualReferenceImages },
  props: {
    modelValue: {
      type: Boolean,
      default: false,
    },
    inlineReference: {
      type: String,
      default: null,
    },
    generating: {
      type: Boolean,
      default: false,
    },
    canGenerate: {
      type: Boolean,
      default: true,
    },
    generationAvailable: {
      type: Boolean,
      default: false,
    },
    editAvailable: {
      type: Boolean,
      default: false,
    },
    maxReferences: {
      type: Number,
      default: 0,
    },
    scene: {
      type: Object,
      required: false,
      default: () => ({}),
    },
    initialRequest: {
      type: Object,
      required: false,
      default: null,
    },
    visualAgentStatus: {
      type: Object,
      required: false,
      default: () => ({}),
    },
    templates: {
      type: Object,
      required: false,
      default: () => ({}),
    },
    // Attachment behaviour for the generated asset, forwarded verbatim as the
    // request's asset_attachment_context. Set when the generation was started
    // from somewhere that owns a target (e.g. a scene message that the image
    // should attach to); left null for the Visual Library's own use.
    attachmentContext: {
      type: Object,
      required: false,
      default: null,
    },
    // True while the backend is still composing the prompt for this request.
    // The prompt fields hold the loading state rather than the dialog, so the
    // user sees the vis type and instructions immediately and can cancel.
    promptLoading: {
      type: Boolean,
      default: false,
    },
    // Prompt composition finished without returning anything. Editing is
    // re-enabled so the dialog is still usable by hand rather than stranded.
    promptFailed: {
      type: Boolean,
      default: false,
    },
  },
  emits: ['update:modelValue'],
  data() {
    return {
      internalModel: this.modelValue,
      mode: 'prompt',
      prompt: '',
      negativePrompt: '',
      instructions: '',
      visType: VIS_TYPE.UNSPECIFIED,
      format: FORMAT_TYPE.LANDSCAPE,
      characterName: '',
      referenceAssets: [],
      // Environment/mood reference (style transfer) - never identity.
      backgroundReferenceAssets: [],
      // The agent's current model ('' = workflow default). Choosing one here
      // sets it globally via set_checkpoint; it is also sent as a per-request
      // extra_config override so the saved asset records what made it and a
      // regenerate reproduces it even after the global choice moves on.
      checkpoint: '',
      checkpointChoices: [],
      // True when a regenerate opened the dialog with its own recorded
      // checkpoint - the agent's current model must not clobber it.
      checkpointFromRequest: false,
      // checkpoint filename -> prompt profile id (dialect), from the backend.
      checkpointProfiles: {},
      // The dialect the currently shown prompt was composed in.
      previewProfile: '',
      // True while a cross-profile checkpoint switch recomposes the prompt.
      recomposing: false,
    };
  },
  computed: {
    canSubmit() {
      if (!this.canGenerate || this.generating || this.promptLoading) return false;

      if (this.mode === 'instruct') {
        // Instruct mode: requires vis_type, and character_name if character vis type
        if (this.isCharacterVisType) {
          return !!(this.characterName && this.characterName.toString().trim());
        }
        return true;
      } else {
        // Prompt mode: requires prompt
        const hasPrompt = !!(this.prompt && this.prompt.toString().trim());
        if (!hasPrompt) return false;
        if (this.isCharacterVisType) {
          return !!(this.characterName && this.characterName.toString().trim());
        }
        const willEdit = (this.inlineReference != null) || (this.referenceAssets && this.referenceAssets.length > 0);
        return willEdit ? this.editAvailable : this.generationAvailable;
      }
    },
    visTypeOptions() {
      return VIS_TYPE_OPTIONS;
    },
    formatOptions() {
      return FORMAT_OPTIONS;
    },
    isCharacterVisType() {
      return (this.visType || '').startsWith('CHARACTER_');
    },
    characterItems() {
      const sc = this.scene || {};
      const chars = (sc && sc.data && Array.isArray(sc.data.characters)) ? sc.data.characters : [];
      return chars.map(c => c && c.name).filter(Boolean);
    },
    availableAssetIds() {
      const sc = this.scene || {};
      const assetsMap = (sc && sc.data && sc.data.assets && sc.data.assets.assets) ? sc.data.assets.assets : {};
      return Object.keys(assetsMap);
    },
    availableAssetsMap() {
      const sc = this.scene || {};
      return (sc && sc.data && sc.data.assets && sc.data.assets.assets) ? sc.data.assets.assets : {};
    },
    isIterateMode() {
      return this.inlineReference != null;
    },
    currentArtStyle() {
      return this.visualAgentStatus?.meta?.current_art_style || null;
    },
    currentArtStyleSource() {
      return this.visualAgentStatus?.meta?.current_art_style_source || null;
    },
    visualStyleTemplates() {
      if(!this.templates || !this.templates.by_type?.visual_style) return [{ value: null, title: 'Use Agent Default' }];
      let templates = Object.values(this.templates.by_type.visual_style)
        .filter((template) => template.visual_type === 'STYLE')
        .map((template) => {
          return {
            value: `${template.group}__${template.uid}`,
            title: template.name,
            props: { subtitle: template.description }
          }
        });
      templates.unshift({ 
        value: null, 
        title: 'Use Agent Default', 
        props: { subtitle: 'Use the default art style from agent configuration.' } 
      });
      return templates;
    },
    sceneVisualStyleTemplate() {
      return this.scene?.data?.visual_style_template || null;
    },
  },
  methods: {
    applyInitialRequest() {
      const r = this.initialRequest;
      if (!r) {
        this.mode = 'prompt';
        this.prompt = '';
        this.negativePrompt = '';
        this.instructions = '';
        this.visType = VIS_TYPE.UNSPECIFIED;
        this.format = FORMAT_TYPE.LANDSCAPE;
        this.characterName = '';
        this.referenceAssets = [];
        this.backgroundReferenceAssets = [];
        return;
      }
      // Instructions present and not iterating: the caller wants the agent to
      // compose the prompt, so start on the instruct tab. A prompt-adjustment
      // request carries both, and wants the prompt tab — it passes
      // prefer_prompt_mode to say so.
      if (r.instructions && r.instructions.trim() && !this.isIterateMode && !r.prefer_prompt_mode) {
        this.mode = 'instruct';
        this.instructions = r.instructions.trim();
      } else {
        this.mode = 'prompt';
        // Prompt-adjustment keeps the source text so the instruct tab still
        // shows what the prompt was composed from; every other caller cleared
        // it here, so leave that alone.
        this.instructions = r.prefer_prompt_mode ? (r.instructions || '').trim() : '';
      }
      this.prompt = r.prompt || '';
      this.negativePrompt = r.negative_prompt || '';
      this.visType = r.vis_type || VIS_TYPE.UNSPECIFIED;
      this.format = r.format || FORMAT_TYPE.LANDSCAPE;
      this.characterName = r.character_name || '';
      this.referenceAssets = (r.reference_assets && Array.isArray(r.reference_assets)) ? r.reference_assets.slice() : [];
      this.backgroundReferenceAssets = (r.background_reference_assets && Array.isArray(r.background_reference_assets)) ? r.background_reference_assets.slice() : [];
      if (r.prompt_profile) this.previewProfile = r.prompt_profile;
      this.recomposing = false;
      // Regenerate keeps the checkpoint the image was made with.
      this.checkpoint = (r.extra_config && r.extra_config.checkpoint) || '';
      this.checkpointFromRequest = Boolean(this.checkpoint);
    },
    requestCheckpoints() {
      this.getWebsocket().send(JSON.stringify({ type: 'visual', action: 'checkpoints' }));
    },
    handleMessage(message) {
      if (message.type !== 'visual' || message.action !== 'checkpoints') return;
      this.checkpointChoices = Array.isArray(message.data) ? message.data : [];
      this.checkpointProfiles = message.profiles || {};
      if (!this.previewProfile && message.current_profile) {
        this.previewProfile = message.current_profile;
      }
      if (!this.checkpointFromRequest) {
        this.checkpoint = message.current || '';
      }
      // A stale choice pointing at a model that no longer exists must not be
      // silently sent; fall back to the workflow default.
      if (this.checkpoint && !this.checkpointChoices.some(c => c.value === this.checkpoint)) {
        this.checkpoint = '';
      }
    },
    onCheckpointChosen(value) {
      this.checkpoint = value || '';
      this.checkpointFromRequest = false;
      // Global, like the art style chip: every generation path runs this model
      // until it is changed again. The backend pairs it with the right sampler
      // settings per generation.
      this.getWebsocket().send(JSON.stringify({
        type: 'visual',
        action: 'set_checkpoint',
        checkpoint: this.checkpoint,
      }));
      // A switch that crosses prompt dialects (e.g. Pony -> Juggernaut) makes
      // the shown prompt wrong for the model - recompose it. Same-dialect
      // switches keep the prompt (and any hand edits).
      const newProfile = this.checkpointProfiles[this.checkpoint] || '';
      if (
        newProfile &&
        this.previewProfile &&
        newProfile !== this.previewProfile &&
        this.attachmentContext &&
        Array.isArray(this.attachmentContext.message_ids) &&
        this.attachmentContext.message_ids.length
      ) {
        this.recomposeForProfile();
      }
    },
    recomposeForProfile() {
      const messageId = this.attachmentContext.message_ids[0];
      const payload = {
        type: 'visual',
        action: 'visualize',
        vis_type: this.visType,
        prompt_only: true,
        return_prompt: true,
        message_ids: [messageId],
      };
      if (this.characterName) payload.character_name = this.characterName;
      if (this.instructions) payload.instructions = this.instructions;
      this.recomposing = true;
      this.getWebsocket().send(JSON.stringify(payload));
    },
    close() {
      this.internalModel = false;
      this.$emit('update:modelValue', false);
    },
    onSubmit() {
      if (!this.canSubmit) return;

      if (this.mode === 'instruct') {
        // Instruct mode: use visualize endpoint
        const payload = {
          type: 'visual',
          action: 'visualize',
          vis_type: this.visType,
          prompt_only: !this.generationAvailable,
        };
        if (this.characterName) {
          payload.character_name = this.characterName;
        }
        if (this.instructions && this.instructions.trim()) {
          payload.instructions = this.instructions.trim();
        }
        // `visualize` takes the attachment target as flat keys rather than a
        // nested context. Without these an adjust-flow request that was
        // submitted from the Instruct tab would generate an image that never
        // attaches to — or is even saved for — the message it started from.
        if (this.attachmentContext) {
          const ctx = this.attachmentContext;
          if (ctx.message_ids && ctx.message_ids.length) {
            payload.message_ids = ctx.message_ids;
          }
          payload.save_asset = true;
          payload.asset_allow_auto_attach = !!ctx.allow_auto_attach;
          payload.asset_allow_override = !!ctx.allow_override;
        }
        this.getWebsocket().send(JSON.stringify(payload));
      } else {
        // Prompt mode: use generate endpoint
        // if references are set gen_type should be IMAGE_EDIT
        const genType = (this.inlineReference != null || this.referenceAssets.length > 0) ? GEN_TYPE.IMAGE_EDIT : GEN_TYPE.TEXT_TO_IMAGE;

        const payload = {
          type: 'visual',
          action: 'generate',
          generation_request: {
            prompt: this.prompt,
            negative_prompt: this.negativePrompt || null,
            vis_type: this.visType,
            gen_type: genType,
            format: this.format,
            character_name: this.isCharacterVisType ? (this.characterName || null) : null,
            reference_assets: this.referenceAssets || [],
            background_reference_assets: this.backgroundReferenceAssets || [],
            inline_reference: this.inlineReference || null,
            // The prompt in the box is what the user saw and approved - possibly
            // hand-edited. Without this the backend's distillation pass recomposes
            // the prompt from scene facts, silently discarding those edits and
            // paying a second LLM call for the privilege.
            distilled: true,
          },
        };
        // Carry the source text through when there is one, so the saved asset
        // records what it was composed from and the library can offer
        // "Regenerate (Instruct)" for it. Callers that clear instructions in
        // prompt mode are unaffected.
        if (this.instructions && this.instructions.trim()) {
          payload.generation_request.instructions = this.instructions.trim();
        }
        if (this.attachmentContext) {
          payload.generation_request.asset_attachment_context = this.attachmentContext;
        }
        if (this.checkpoint) {
          payload.generation_request.extra_config = { checkpoint: this.checkpoint };
        }
        this.getWebsocket().send(JSON.stringify(payload));
      }
      this.close();
    },
    updateArtStyle(templateValue) {
      this.getWebsocket().send(JSON.stringify({
        type: 'visual',
        action: 'update_art_style',
        visual_style_template: templateValue,
      }));
    },
  },
  mounted() {
    this.registerMessageHandler(this.handleMessage);
  },
  unmounted() {
    this.unregisterMessageHandler(this.handleMessage);
  },
  watch: {
    modelValue(newVal) {
      this.internalModel = newVal;
      if (newVal) {
        this.applyInitialRequest();
        this.requestCheckpoints();
      }
    },
    // A prompt-adjustment request opens the dialog before the backend has
    // composed the prompt, then fills it in when the preview arrives. Every
    // field this re-applies is disabled while promptLoading, so it cannot
    // discard user input — if you make one of them editable during loading,
    // this watcher will start clobbering it.
    initialRequest() {
      if (this.internalModel) {
        this.applyInitialRequest();
      }
    },
    internalModel(newVal) {
      this.$emit('update:modelValue', newVal);
    },
    inlineReference(newVal) {
      // If iterate mode becomes active and we're in instruct mode, switch to prompt mode
      if (newVal != null && this.mode === 'instruct') {
        this.mode = 'prompt';
      }
    },
  },
};
</script>

<style scoped>
</style>


