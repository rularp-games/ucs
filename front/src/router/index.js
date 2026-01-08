import { createRouter, createWebHistory } from 'vue-router'
import HelloWorld from '../components/HelloWorld.vue'
import Cyberwordly from '../components/Cyberwordly.vue'
import Drones from '../components/Drones.vue'
import Airlock from '../components/Airlock.vue'

const routes = [
  {
    path: '/',
    name: 'Home',
    component: HelloWorld,
    props: { msg: 'Welcome to Your Vue.js App' }
  },
  {
    path: '/cyberwordly',
    name: 'Cyberwordly',
    component: Cyberwordly
  },
  {
    path: '/drones',
    name: 'Drones',
    component: Drones
  },
  {
    path: '/airlock',
    name: 'Airlock',
    component: Airlock
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

export default router

