<template>
  <div class="glass glass-spotlight bento-card" @mousemove="onSpotMove">
    <slot />
  </div>
</template>

<script setup lang="ts">
function onSpotMove(e: MouseEvent) {
  const el = e.currentTarget as HTMLElement;
  const rect = el.getBoundingClientRect();
  el.style.setProperty("--spot-x", `${e.clientX - rect.left}px`);
  el.style.setProperty("--spot-y", `${e.clientY - rect.top}px`);
}
</script>
