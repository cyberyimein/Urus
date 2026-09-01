<template>
  <RouterView v-slot="{ Component, route }">
    <!--
      Indicator and strategy pages intentionally share a view component, but
      they are different workspaces. Key by route name so Vue Router cannot
      retain the indicator component tree when entering the strategy surface.
      Parameter/query changes within one surface are handled by that view's
      own route watcher.
    -->
    <component :is="Component" :key="route.name" />
  </RouterView>
</template>
