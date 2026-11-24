<template>
  <el-dialog
    :model-value="visible"
    title="快捷命令面板 (⌘K / Ctrl+K)"
    width="520px"
    append-to-body
    @update:model-value="$emit('update:visible', $event)"
  >
    <el-input
      v-model="query"
      placeholder="输入命令筛选：例如 星云、霞光、示例、导出、历史..."
      clearable
      autofocus
      style="margin-bottom: 12px"
    />
    <div v-auto-animate style="display: flex; flex-direction: column; gap: 8px; max-height: 320px; overflow: auto">
      <button
        v-for="cmd in filteredCommands"
        :key="cmd.id"
        type="button"
        class="queue-item"
        style="width: 100%; cursor: pointer; text-align: left; color: var(--glass-text)"
        @click="runCommand(cmd)"
      >
        <div>
          <div style="font-weight: 600; font-size: 14px">{{ cmd.label }}</div>
          <div class="glass-muted" style="font-size: 12px">{{ cmd.hint }}</div>
        </div>
        <span class="gem-pill pill-sky">{{ cmd.tag }}</span>
      </button>
      <el-empty v-if="filteredCommands.length === 0" description="未匹配到命令" :image-size="64" />
    </div>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";

export interface CommandItem {
  id: string;
  label: string;
  hint: string;
  tag: string;
  action: () => void;
}

const props = defineProps<{
  visible: boolean;
  commands: CommandItem[];
}>();

const emit = defineEmits<{
  (e: "update:visible", val: boolean): void;
}>();

const query = ref("");

const filteredCommands = computed(() => {
  const q = query.value.trim().toLowerCase();
  if (!q) return props.commands;
  return props.commands.filter(
    (c) =>
      c.label.toLowerCase().includes(q) ||
      c.hint.toLowerCase().includes(q) ||
      c.tag.toLowerCase().includes(q)
  );
});

function runCommand(cmd: CommandItem) {
  cmd.action();
  emit("update:visible", false);
  query.value = "";
}
</script>
