<template>
  <div id="app">
    <nav>
      <router-link to="/cyberwordly">Cyberwordly</router-link> |
      <router-link to="/drones">Drones</router-link>
    </nav>
    <router-view/>
  </div>
</template>

<script>
export default {
  name: 'App'
}
</script>

<style>
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  background-color: #000000;
  margin: 0;
  padding: 0;
}

#app {
  font-family: Avenir, Helvetica, Arial, sans-serif;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  min-height: 100vh;
  background-color: #000000;
}

nav {
  padding: 20px;
  background-color: #000000;
  z-index: 100;
  position: relative;
}

nav a {
  color: #00ff00;
  text-decoration: none;
  margin: 0 10px;
  font-weight: bold;
}

nav a.router-link-active {
  color: #42b983;
}
</style>
