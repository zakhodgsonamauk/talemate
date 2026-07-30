<template>
    <v-textarea
        v-model="visualAnchor"
        label="Appearance Keywords"
        hint="Comma-delimited. Injected unchanged into every image this character appears in"
        rows="3"
        auto-grow
        variant="outlined"
        @blur="saveVisualAnchor"
        :loading="isSavingAnchor"
        persistent-hint
        class="mt-4"
    >
        <template v-slot:append-inner>
            <v-btn
                icon="mdi-auto-fix"
                size="small"
                variant="text"
                density="comfortable"
                :loading="isDeriving"
                title="Generate from this character's appearance description"
                @mousedown.prevent
                @click="deriveVisualAnchor"
            ></v-btn>
        </template>
    </v-textarea>

    <v-card variant="outlined" color="muted" class="mt-4">
        <v-card-text>
            <div class="d-flex align-start">
                <v-icon class="mr-3 mt-1" color="primary">mdi-information-outline</v-icon>
                <div class="text-muted">
                    <div class="text-primary text-subtitle-2 font-weight-bold mb-1">About Appearance Keywords</div>
                    <p class="text-body-2 mb-2">
                        This is what keeps the character recognisable between images. The same keywords go into
                        <strong>every</strong> prompt they appear in, so the image model draws the same person each time.
                    </p>
                    <p class="text-body-2 mb-2">
                        Leave it empty and it will be generated from the character's appearance attribute the first
                        time an image needs it, then reused. Use the
                        <v-icon size="x-small">mdi-auto-fix</v-icon> button to regenerate it after editing their
                        appearance.
                    </p>
                    <p class="text-body-2 mb-0">
                        <strong>Example:</strong> "alien woman, deep violet skin, geometric facial markings, indigo hair pulled back, four-fingered hands, fitted dark blue-grey utility suit"
                    </p>
                </div>
            </div>
        </v-card-text>
    </v-card>

    <v-divider class="mt-6"></v-divider>

    <v-textarea
        v-model="visualRules"
        label="Static Visual Rules"
        hint="Describe permanent physical traits or anatomical rules that never change"
        rows="10"
        auto-grow
        variant="outlined"
        @blur="saveVisualRules"
        :loading="isSaving"
        persistent-hint
        class="mt-4"
    ></v-textarea>

    <v-card variant="outlined" color="muted" class="mt-4">
        <v-card-text>
            <div class="d-flex align-start">
                <v-icon class="mr-3 mt-1" color="primary">mdi-information-outline</v-icon>
                <div class="text-muted">
                    <div class="text-primary text-subtitle-2 font-weight-bold mb-1">About Static Visual Rules</div>
                    <p class="text-body-2 mb-2">
                        These rules define permanent physical traits that are enforced for <strong>every</strong> image generated for this character.
                    </p>
                    <p class="text-body-2 mb-2">
                        <v-icon size="x-small" color="warning" class="mr-1">mdi-alert-circle-outline</v-icon>
                        <strong>Important:</strong> Do NOT include clothing details, art styles, or anything that might change between scenes.
                    </p>
                    <p class="text-body-2 mb-0">
                        <strong>Examples:</strong> "Always has a cybernetic left arm", "Has a distinct birthmark on their neck", "Has heterochromia (left eye blue, right eye green)"
                    </p>
                </div>
            </div>
        </v-card-text>
    </v-card>
</template>

<script>
export default {
    name: 'WorldStateManagerCharacterVisualsRules',
    props: {
        character: Object,
        scene: Object,
    },
    data() {
        return {
            visualRules: this.character?.visual_rules || '',
            visualAnchor: this.character?.visual_anchor || '',
            isSaving: false,
            isSavingAnchor: false,
            isDeriving: false,
        }
    },
    inject: ['getWebsocket'],
    watch: {
        'character.visual_rules': {
            handler(newVal) {
                if (newVal !== this.visualRules) {
                    this.visualRules = newVal || '';
                }
            },
            immediate: true,
        },
        'character.visual_anchor': {
            handler(newVal) {
                if (newVal !== this.visualAnchor) {
                    this.visualAnchor = newVal || '';
                }
                // A derive round-trip lands here, so this is where the spinner stops.
                this.isDeriving = false;
            },
            immediate: true,
        },
    },
    methods: {
        saveVisualAnchor() {
            if (this.visualAnchor === (this.character?.visual_anchor || '')) return;

            this.isSavingAnchor = true;
            this.getWebsocket().send(JSON.stringify({
                type: 'world_state_manager',
                action: 'update_character_visual_anchor',
                name: this.character.name,
                visual_anchor: this.visualAnchor,
            }));

            setTimeout(() => {
                this.isSavingAnchor = false;
            }, 500);
        },
        deriveVisualAnchor() {
            this.isDeriving = true;
            this.getWebsocket().send(JSON.stringify({
                type: 'world_state_manager',
                action: 'derive_character_visual_anchor',
                name: this.character.name,
            }));

            // A failed derive leaves the anchor untouched, so the watcher never fires
            // and the spinner would run forever. The backend surfaces its own error.
            clearTimeout(this.deriveTimeout);
            this.deriveTimeout = setTimeout(() => {
                this.isDeriving = false;
            }, 30000);
        },
        saveVisualRules() {
            if (this.visualRules === this.character?.visual_rules) return;
            
            this.isSaving = true;
            this.getWebsocket().send(JSON.stringify({
                type: 'world_state_manager',
                action: 'update_character_visual_rules',
                name: this.character.name,
                visual_rules: this.visualRules,
            }));
            
            // We don't necessarily need to wait for a response to stop loading if we trust the WS
            // but the parent will update the prop when the change is broadcasted.
            setTimeout(() => {
                this.isSaving = false;
            }, 500);
        }
    }
}
</script>
