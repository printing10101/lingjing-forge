<template>
  <el-dialog
    :model-value="visible"
    :title="t('nlInputPanel.editModelParamsTitle')"
    width="500px"
    @update:model-value="emit('update:visible', $event)"
  >
    <el-form :model="localParams" label-width="100px">
      <el-form-item :label="t('nlInputPanel.shapeTypeFormLabel')">
        <el-select v-model="localParams.shape_type">
          <el-option :label="t('nlInputPanel.optionBox')" value="box" />
          <el-option
            :label="t('nlInputPanel.optionCylinder')"
            value="cylinder"
          />
          <el-option :label="t('nlInputPanel.optionSphere')" value="sphere" />
          <el-option :label="t('nlInputPanel.optionCone')" value="cone" />
        </el-select>
      </el-form-item>
      <el-form-item
        v-if="localParams.dimensions"
        :label="t('nlInputPanel.lengthLabel')"
      >
        <el-input-number
          v-model="localParams.dimensions.length"
          :min="1"
          :max="1000"
        />
      </el-form-item>
      <el-form-item
        v-if="localParams.dimensions"
        :label="t('nlInputPanel.widthLabel')"
      >
        <el-input-number
          v-model="localParams.dimensions.width"
          :min="1"
          :max="1000"
        />
      </el-form-item>
      <el-form-item
        v-if="localParams.dimensions"
        :label="t('nlInputPanel.heightLabel')"
      >
        <el-input-number
          v-model="localParams.dimensions.height"
          :min="1"
          :max="1000"
        />
      </el-form-item>
      <el-form-item
        v-if="localParams.dimensions"
        :label="t('nlInputPanel.radiusLabel')"
      >
        <el-input-number
          v-model="localParams.dimensions.radius"
          :min="1"
          :max="500"
        />
      </el-form-item>
      <el-form-item :label="t('nlInputPanel.materialFormLabel')">
        <el-input
          v-model="localParams.material"
          :placeholder="t('nlInputPanel.materialPlaceholder')"
        />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="emit('update:visible', false)">
        {{ t("common.cancel") }}
      </el-button>
      <el-button type="primary" @click="emit('confirm', localParams)">
        {{ t("nlInputPanel.confirmEdit") }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { watch, ref, toRaw } from "vue";
import { useI18n } from "vue-i18n";
import type { CADParams } from "@/types/nl2cad";

defineOptions({ name: "ParamEditDialog" });

const props = defineProps<{
  visible: boolean;
  params: CADParams;
}>();

const emit = defineEmits<{
  (e: "update:visible", visible: boolean): void;
  (e: "confirm", params: CADParams): void;
}>();

const { t } = useI18n();

const localParams = ref<CADParams>({} as CADParams);

watch(
  () => props.params,
  (val) => {
    if (val && Object.keys(val).length > 0) {
      localParams.value = structuredClone(toRaw(val));
    }
  },
  { immediate: true },
);
</script>
