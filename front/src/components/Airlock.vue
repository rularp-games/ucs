<template>
  <div class="airlock">
    <!-- Фоновые линии -->
    <div class="background-lines">
      <div 
        v-for="line in backgroundLines" 
        :key="line.id" 
        class="bg-line"
        :style="{ 
          left: line.left + '%', 
          animationDuration: line.duration + 's',
          animationDelay: line.delay + 's',
          opacity: line.opacity
        }"
      ></div>
    </div>

    <div class="content-wrapper">
      <h1 class="title">УПРАВЛЕНИЕ ШЛЮЗОМ</h1>
      <p class="subtitle">AIRLOCK CONTROL SYSTEM v2.5.0</p>
      <p class="object-info" v-if="currentObjectId">
        {{ currentObjectName || 'Airlock' }} #{{ currentObjectId }}
        <span v-if="projectName"> | Проект: {{ projectName }}</span>
      </p>

      <!-- Визуализация шлюза -->
      <div class="airlock-visual">
        <div class="airlock-chamber" :class="chamberClass">
          <!-- Внешняя дверь -->
          <div class="door door-outer" :class="{ 'door-open': outerDoorOpen }">
            <div class="door-label">OUTER</div>
            <div class="door-indicator" :class="outerDoorOpen ? 'indicator-open' : 'indicator-closed'"></div>
          </div>
          
          <!-- Камера шлюза -->
          <div class="chamber-body">
            <div class="chamber-content">
              <div class="pressure-gauge">
                <div class="gauge-label">ДАВЛЕНИЕ</div>
                <div class="gauge-value">{{ pressure.toFixed(1) }} atm</div>
                <div class="gauge-bar">
                  <div class="gauge-fill" :style="{ width: (pressure * 100) + '%' }"></div>
                </div>
              </div>
              
              <div v-if="cycleInProgress" class="cycle-indicator">
                <div class="cycle-label">{{ cycleDirection === 'depressurize' ? 'ДЕКОМПРЕССИЯ' : 'КОМПРЕССИЯ' }}</div>
                <div class="cycle-progress">{{ cycleProgress }}%</div>
                <div class="progress-bar">
                  <div class="progress-fill" :style="{ width: cycleProgress + '%' }"></div>
                </div>
              </div>
            </div>
          </div>
          
          <!-- Внутренняя дверь -->
          <div class="door door-inner" :class="{ 'door-open': innerDoorOpen }">
            <div class="door-label">INNER</div>
            <div class="door-indicator" :class="innerDoorOpen ? 'indicator-open' : 'indicator-closed'"></div>
          </div>
        </div>
      </div>

      <!-- Статус панель -->
      <div class="status-panel">
        <div class="status-row">
          <span class="status-label">СТАТУС:</span>
          <span class="status-value" :class="statusClass">{{ statusText }}</span>
        </div>
        <div class="status-row">
          <span class="status-label">БЛОКИРОВКА:</span>
          <span class="status-value" :class="locked ? 'status-locked' : 'status-unlocked'">
            {{ locked ? 'АКТИВНА' : 'СНЯТА' }}
          </span>
        </div>
        <div class="status-row">
          <span class="status-label">ВНЕШНЯЯ ДВЕРЬ:</span>
          <span class="status-value" :class="outerDoorOpen ? 'status-open' : 'status-closed'">
            {{ outerDoorOpen ? 'ОТКРЫТА' : 'ЗАКРЫТА' }}
          </span>
        </div>
        <div class="status-row">
          <span class="status-label">ВНУТРЕННЯЯ ДВЕРЬ:</span>
          <span class="status-value" :class="innerDoorOpen ? 'status-open' : 'status-closed'">
            {{ innerDoorOpen ? 'ОТКРЫТА' : 'ЗАКРЫТА' }}
          </span>
        </div>
      </div>

      <!-- Панель управления -->
      <div class="control-panel">
        <div class="control-section">
          <h3>БЛОКИРОВКА</h3>
          <button 
            class="control-btn" 
            :class="locked ? 'btn-unlock' : 'btn-lock'"
            @click="toggleLock"
            :disabled="cycleInProgress"
          >
            {{ locked ? '🔓 РАЗБЛОКИРОВАТЬ' : '🔒 ЗАБЛОКИРОВАТЬ' }}
          </button>
        </div>

        <div class="control-section">
          <h3>ВНЕШНЯЯ ДВЕРЬ</h3>
          <button 
            class="control-btn"
            :class="outerDoorOpen ? 'btn-close' : 'btn-open'"
            @click="toggleOuterDoor"
            :disabled="!canOperateOuterDoor"
          >
            {{ outerDoorOpen ? '◀▶ ЗАКРЫТЬ' : '▶◀ ОТКРЫТЬ' }}
          </button>
        </div>

        <div class="control-section">
          <h3>ВНУТРЕННЯЯ ДВЕРЬ</h3>
          <button 
            class="control-btn"
            :class="innerDoorOpen ? 'btn-close' : 'btn-open'"
            @click="toggleInnerDoor"
            :disabled="!canOperateInnerDoor"
          >
            {{ innerDoorOpen ? '◀▶ ЗАКРЫТЬ' : '▶◀ ОТКРЫТЬ' }}
          </button>
        </div>

        <div class="control-section cycle-section">
          <h3>ШЛЮЗОВАНИЕ</h3>
          <button 
            class="control-btn btn-cycle"
            @click="startCycle"
            :disabled="!canStartCycle"
          >
            {{ cycleInProgress ? '⏳ В ПРОЦЕССЕ...' : '🔄 НАЧАТЬ ЦИКЛ' }}
          </button>
          <button 
            v-if="cycleInProgress"
            class="control-btn btn-abort"
            @click="abortCycle"
          >
            ⛔ ПРЕРВАТЬ
          </button>
        </div>
      </div>

      <!-- Лог событий -->
      <div class="log-panel">
        <h3>СИСТЕМНЫЙ ЛОГ</h3>
        <div class="log-content">
          <div 
            v-for="(entry, index) in log" 
            :key="index" 
            class="log-entry"
            :class="'log-' + entry.type"
          >
            <span class="log-time">{{ entry.time }}</span>
            <span class="log-message">{{ entry.message }}</span>
          </div>
        </div>
      </div>

      <!-- Справка -->
      <div class="help-section">
        <h3>Протокол шлюзования</h3>
        <p>1. Снимите блокировку для операций</p>
        <p>2. Закройте обе двери перед циклом</p>
        <p>3. Запустите шлюзование</p>
        <p>4. Дождитесь завершения декомпрессии</p>
        <p>5. Откройте внешнюю дверь</p>
        <p class="warning">⚠️ Одновременное открытие дверей запрещено!</p>
      </div>

      <!-- Настройка API -->
      <div class="help-section" v-if="apiError">
        <h3>Настройка Django</h3>
        <p>Создайте в admin panel:</p>
        <p>1. Project (любой)</p>
        <p>2. Object: name = "Airlock"</p>
        <p>Свойства создадутся автоматически!</p>
        <p class="url-hint">Или используйте URL:</p>
        <p class="url-example">/airlock/ProjectName/ObjectId</p>
      </div>

      <!-- Создание свойств -->
      <div class="help-section creating-info" v-if="creatingProperties">
        <h3>Создание свойств...</h3>
        <p>Пожалуйста, подождите</p>
      </div>

      <!-- Celery задача активна -->
      <div class="help-section task-info" v-if="currentTaskId">
        <h3>Активная задача</h3>
        <p>ID: {{ currentTaskId.substring(0, 8) }}...</p>
        <p>Прогресс: {{ cycleProgress }}%</p>
      </div>
    </div>
  </div>
</template>

<script>
// Конфигурация API
const API_BASE_URL = '/api'
const DEFAULT_OBJECT_NAME = 'Airlock'
const POLLING_INTERVAL = 500 // мс
const TASK_POLLING_INTERVAL = 250 // мс - для отслеживания прогресса задачи

// Определение необходимых свойств шлюза
const REQUIRED_PROPERTIES = [
  { name: 'locked', type: 'boolean', defaultValue: false, description: 'Блокировка шлюза' },
  { name: 'outer_door_open', type: 'boolean', defaultValue: false, description: 'Состояние внешней двери' },
  { name: 'inner_door_open', type: 'boolean', defaultValue: false, description: 'Состояние внутренней двери' },
  { name: 'pressure', type: 'number', defaultValue: 1.0, description: 'Давление в камере шлюза (0.0 - 1.0 atm)' }
]

export default {
  name: 'AirlockControl',
  props: {
    // Параметры из URL
    projectName: {
      type: String,
      default: null
    },
    objectId: {
      type: [String, Number],
      default: null
    }
  },
  data() {
    return {
      // Состояние шлюза
      locked: true,
      outerDoorOpen: false,
      innerDoorOpen: false,
      pressure: 1.0,
      cycleInProgress: false,
      cycleProgress: 0,
      cycleDirection: null,
      
      // API данные
      currentObjectId: null,
      currentProjectId: null,
      currentObjectName: null,
      properties: {}, // { name: { id, value } }
      apiConnected: false,
      apiError: null,
      loading: true,
      creatingProperties: false,
      
      // Celery задача
      currentTaskId: null,
      taskPollingInterval: null,
      
      // Polling
      pollingInterval: null,
      lastPressure: null,
      
      // UI
      log: [],
      backgroundLines: []
    }
  },
  computed: {
    statusText() {
      if (!this.apiConnected) return 'НЕТ СВЯЗИ С СЕРВЕРОМ'
      if (this.loading) return 'ЗАГРУЗКА...'
      if (this.cycleInProgress) {
        return this.cycleDirection === 'depressurize' ? 'ДЕКОМПРЕССИЯ' : 'КОМПРЕССИЯ'
      }
      if (this.locked) return 'ЗАБЛОКИРОВАН'
      if (this.outerDoorOpen) return 'ВНЕШНЯЯ ДВЕРЬ ОТКРЫТА'
      if (this.innerDoorOpen) return 'ВНУТРЕННЯЯ ДВЕРЬ ОТКРЫТА'
      if (this.pressure < 0.1) return 'ВАКУУМ'
      if (this.pressure >= 1.0) return 'ГОТОВ'
      return 'ЧАСТИЧНОЕ ДАВЛЕНИЕ'
    },
    statusClass() {
      if (!this.apiConnected) return 'status-error'
      if (this.loading) return 'status-loading'
      if (this.cycleInProgress) return 'status-cycling'
      if (this.locked) return 'status-locked'
      if (this.outerDoorOpen || this.innerDoorOpen) return 'status-warning'
      if (this.pressure < 0.1) return 'status-vacuum'
      return 'status-ready'
    },
    chamberClass() {
      return {
        'chamber-locked': this.locked,
        'chamber-cycling': this.cycleInProgress,
        'chamber-vacuum': this.pressure < 0.1,
        'chamber-pressurized': this.pressure >= 1.0,
        'chamber-disconnected': !this.apiConnected
      }
    },
    canOperateOuterDoor() {
      if (!this.apiConnected || this.loading) return false
      if (this.locked || this.cycleInProgress) return false
      if (this.outerDoorOpen) return true
      return !this.innerDoorOpen && this.pressure < 0.1
    },
    canOperateInnerDoor() {
      if (!this.apiConnected || this.loading) return false
      if (this.locked || this.cycleInProgress) return false
      if (this.innerDoorOpen) return true
      return !this.outerDoorOpen && this.pressure >= 1.0
    },
    canStartCycle() {
      if (!this.apiConnected || this.loading) return false
      return !this.locked && !this.outerDoorOpen && !this.innerDoorOpen && !this.cycleInProgress
    }
  },
  mounted() {
    document.title = 'Airlock Control'
    this.generateBackgroundLines()
    this.initializeFromAPI()
  },
  beforeUnmount() {
    this.stopPolling()
    this.stopTaskPolling()
  },
  methods: {
    // ==================== API методы ====================
    
    async initializeFromAPI() {
      this.loading = true
      this.addLog('info', 'Подключение к серверу...')
      
      try {
        let airlockObject = null
        
        // Если передан objectId из URL - используем его напрямую
        if (this.objectId) {
          this.addLog('info', `Загрузка объекта ID: ${this.objectId}`)
          
          const response = await fetch(`${API_BASE_URL}/objects/${this.objectId}/`)
          if (!response.ok) {
            throw new Error(`Объект с ID ${this.objectId} не найден`)
          }
          
          airlockObject = await response.json()
          this.currentObjectName = airlockObject.name
          
        } else {
          // Ищем объект по имени (по умолчанию "Airlock")
          const objectName = DEFAULT_OBJECT_NAME
          this.addLog('info', `Поиск объекта "${objectName}"...`)
          
          const objectsResponse = await fetch(`${API_BASE_URL}/objects/?name=${encodeURIComponent(objectName)}`)
          if (!objectsResponse.ok) throw new Error('Ошибка получения объектов')
          
          const objects = await objectsResponse.json()
          
          if (objects.length === 0) {
            this.apiError = `Объект "${objectName}" не найден. Создайте его через admin.`
            this.addLog('error', this.apiError)
            this.loading = false
            return
          }
          
          airlockObject = objects[0]
          this.currentObjectName = objectName
        }
        
        this.currentObjectId = airlockObject.id
        this.currentProjectId = airlockObject.project?.id
        
        const projectInfo = this.projectName ? ` (проект: ${this.projectName})` : ''
        this.addLog('success', `Объект "${airlockObject.name}" найден (ID: ${this.currentObjectId})${projectInfo}`)
        
        // Загружаем свойства
        await this.loadProperties()
        
        // Проверяем и создаём недостающие свойства
        await this.ensureRequiredProperties()
        
        this.apiConnected = true
        this.loading = false
        this.addLog('success', 'Система управления шлюзом инициализирована')
        
        // Запускаем polling для отслеживания изменений давления
        this.startPolling()
        
      } catch (error) {
        this.apiError = error.message
        this.apiConnected = false
        this.loading = false
        this.addLog('error', `Ошибка подключения: ${error.message}`)
      }
    },
    
    async loadProperties() {
      try {
        const response = await fetch(`${API_BASE_URL}/objects/${this.currentObjectId}/properties/`)
        if (!response.ok) throw new Error('Ошибка загрузки свойств')
        
        const propertiesArray = await response.json()
        
        // Преобразуем в объект по имени для удобства
        this.properties = {}
        for (const prop of propertiesArray) {
          this.properties[prop.name] = {
            id: prop.id,
            type: prop.property_type,
            value: prop.value
          }
        }
        
        // Синхронизируем локальное состояние с сервером
        this.syncStateFromProperties()
        
      } catch (error) {
        throw new Error(`Ошибка загрузки свойств: ${error.message}`)
      }
    },
    
    async ensureRequiredProperties() {
      /**
       * Проверяет наличие необходимых свойств и создаёт отсутствующие
       */
      const missingProps = REQUIRED_PROPERTIES.filter(prop => !this.properties[prop.name])
      
      if (missingProps.length === 0) {
        this.addLog('info', 'Все необходимые свойства найдены')
        return
      }
      
      this.addLog('warning', `Отсутствуют свойства: ${missingProps.map(p => p.name).join(', ')}`)
      this.creatingProperties = true
      
      for (const propDef of missingProps) {
        try {
          await this.createProperty(propDef)
          this.addLog('success', `Свойство "${propDef.name}" создано`)
        } catch (error) {
          this.addLog('error', `Ошибка создания "${propDef.name}": ${error.message}`)
        }
      }
      
      // Перезагружаем свойства после создания
      await this.loadProperties()
      this.creatingProperties = false
    },
    
    async createProperty(propDef) {
      /**
       * Создаёт новое свойство для объекта
       * propDef: { name, type, defaultValue, description }
       */
      const body = {
        object_id: this.currentObjectId,
        name: propDef.name,
        description: propDef.description,
        property_type: propDef.type
      }
      
      // Устанавливаем значение по умолчанию в зависимости от типа
      if (propDef.type === 'boolean') {
        body.value_boolean = propDef.defaultValue
      } else if (propDef.type === 'number') {
        body.value_number = propDef.defaultValue
      } else if (propDef.type === 'text') {
        body.value_text = propDef.defaultValue
      }
      
      const response = await fetch(`${API_BASE_URL}/properties/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      })
      
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(errorData.detail || errorData.error || 'Ошибка создания свойства')
      }
      
      return await response.json()
    },
    
    syncStateFromProperties() {
      if (this.properties.locked) {
        this.locked = Boolean(this.properties.locked.value)
      }
      if (this.properties.outer_door_open) {
        this.outerDoorOpen = Boolean(this.properties.outer_door_open.value)
      }
      if (this.properties.inner_door_open) {
        this.innerDoorOpen = Boolean(this.properties.inner_door_open.value)
      }
      if (this.properties.pressure) {
        const newPressure = Number(this.properties.pressure.value) || 0
        
        // Определяем прогресс цикла на основе изменения давления
        if (this.cycleInProgress && this.lastPressure !== null) {
          if (this.cycleDirection === 'depressurize') {
            this.cycleProgress = Math.round((1 - newPressure) * 100)
          } else {
            this.cycleProgress = Math.round(newPressure * 100)
          }
          
          // Проверяем завершение цикла
          if ((this.cycleDirection === 'depressurize' && newPressure <= 0) ||
              (this.cycleDirection === 'pressurize' && newPressure >= 1)) {
            this.completeCycle()
          }
        }
        
        this.pressure = newPressure
        this.lastPressure = newPressure
      }
    },
    
    async updateProperty(name, value) {
      if (!this.properties[name]) {
        this.addLog('error', `Свойство "${name}" не найдено`)
        return false
      }
      
      const propId = this.properties[name].id
      const propType = this.properties[name].type
      
      // Формируем тело запроса в зависимости от типа
      let body = {}
      if (propType === 'boolean') {
        body.value_boolean = value
      } else if (propType === 'number') {
        body.value_number = value
      } else if (propType === 'text') {
        body.value_text = value
      }
      
      try {
        const response = await fetch(`${API_BASE_URL}/properties/${propId}/`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        })
        
        if (!response.ok) throw new Error('Ошибка обновления')
        
        // Обновляем локальный кэш
        this.properties[name].value = value
        return true
        
      } catch (error) {
        this.addLog('error', `Ошибка сохранения "${name}": ${error.message}`)
        return false
      }
    },
    
    async bulkUpdateProperties(updates) {
      /**
       * Атомарное обновление нескольких свойств
       * updates: [{ name: 'prop_name', value: ... }, ...]
       */
      const properties = updates.map(update => {
        const prop = this.properties[update.name]
        if (!prop) return null
        
        const propData = { name: update.name }
        if (prop.type === 'boolean') {
          propData.value_boolean = update.value
        } else if (prop.type === 'number') {
          propData.value_number = update.value
        } else if (prop.type === 'text') {
          propData.value_text = update.value
        }
        return propData
      }).filter(p => p !== null)
      
      if (properties.length === 0) {
        this.addLog('error', 'Нет свойств для обновления')
        return false
      }
      
      try {
        const response = await fetch(`${API_BASE_URL}/properties/bulk-update/`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            object_id: this.currentObjectId,
            properties: properties
          })
        })
        
        if (!response.ok) {
          const errorData = await response.json()
          throw new Error(errorData.error || 'Ошибка обновления')
        }
        
        // Обновляем локальный кэш
        for (const update of updates) {
          if (this.properties[update.name]) {
            this.properties[update.name].value = update.value
          }
        }
        
        return true
        
      } catch (error) {
        this.addLog('error', `Ошибка bulk update: ${error.message}`)
        return false
      }
    },
    
    async startPressureChange(targetValue) {
      if (!this.properties.pressure) {
        this.addLog('error', 'Свойство "pressure" не найдено')
        return false
      }
      
      const propId = this.properties.pressure.id
      
      try {
        const response = await fetch(`${API_BASE_URL}/properties/${propId}/change-value/`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            target_value: targetValue,
            step: 0.01,      // 1% за шаг
            interval: 0.1   // 0.1 секунды между шагами
          })
        })
        
        if (!response.ok) {
          const errorData = await response.json()
          throw new Error(errorData.error || 'Ошибка запуска цикла')
        }
        
        const result = await response.json()
        this.currentTaskId = result.task_id
        this.addLog('info', `Celery задача запущена: ${result.task_id.substring(0, 8)}...`)
        
        // Запускаем polling статуса задачи для отслеживания прогресса
        this.startTaskPolling()
        
        return true
        
      } catch (error) {
        this.addLog('error', `Ошибка запуска цикла: ${error.message}`)
        return false
      }
    },
    
    async getTaskStatus() {
      if (!this.currentTaskId) return null
      
      try {
        const response = await fetch(`${API_BASE_URL}/tasks/${this.currentTaskId}/`)
        if (!response.ok) throw new Error('Ошибка получения статуса')
        
        return await response.json()
      } catch (error) {
        console.error('Task status error:', error)
        return null
      }
    },
    
    async cancelTask() {
      if (!this.currentTaskId) return false
      
      try {
        const response = await fetch(`${API_BASE_URL}/tasks/${this.currentTaskId}/?terminate=true`, {
          method: 'DELETE'
        })
        
        if (!response.ok) throw new Error('Ошибка отмены задачи')
        
        this.addLog('warning', 'Celery задача отменена')
        return true
        
      } catch (error) {
        this.addLog('error', `Ошибка отмены задачи: ${error.message}`)
        return false
      }
    },
    
    startTaskPolling() {
      this.stopTaskPolling() // Останавливаем предыдущий polling если есть
      
      this.taskPollingInterval = setInterval(async () => {
        const status = await this.getTaskStatus()
        if (!status) return
        
        // Обновляем прогресс из статуса задачи
        if (status.status === 'PROGRESS' && status.result) {
          this.cycleProgress = status.result.progress || 0
        }
        
        // Проверяем завершение
        if (status.ready || status.status === 'SUCCESS' || status.status === 'REVOKED') {
          this.stopTaskPolling()
          
          if (status.status === 'SUCCESS') {
            this.completeCycle()
          } else if (status.status === 'REVOKED') {
            this.addLog('warning', 'Задача была отменена')
            this.cycleInProgress = false
            this.cycleDirection = null
            this.cycleProgress = 0
          }
          
          this.currentTaskId = null
        }
      }, TASK_POLLING_INTERVAL)
    },
    
    stopTaskPolling() {
      if (this.taskPollingInterval) {
        clearInterval(this.taskPollingInterval)
        this.taskPollingInterval = null
      }
    },
    
    startPolling() {
      this.pollingInterval = setInterval(async () => {
        if (!this.apiConnected) return
        
        try {
          await this.loadProperties()
        } catch (error) {
          console.error('Polling error:', error)
        }
      }, POLLING_INTERVAL)
    },
    
    stopPolling() {
      if (this.pollingInterval) {
        clearInterval(this.pollingInterval)
        this.pollingInterval = null
      }
    },
    
    // ==================== Управление шлюзом ====================
    
    async toggleLock() {
      const newValue = !this.locked
      
      if (await this.updateProperty('locked', newValue)) {
        this.locked = newValue
        if (this.locked) {
          this.addLog('warning', 'Блокировка активирована')
        } else {
          this.addLog('info', 'Блокировка снята. Система готова к работе.')
        }
      }
    },
    
    async toggleOuterDoor() {
      if (!this.canOperateOuterDoor && !this.outerDoorOpen) return
      
      const newValue = !this.outerDoorOpen
      
      if (await this.updateProperty('outer_door_open', newValue)) {
        this.outerDoorOpen = newValue
        if (this.outerDoorOpen) {
          this.addLog('warning', 'Внешняя дверь ОТКРЫТА')
        } else {
          this.addLog('info', 'Внешняя дверь закрыта')
        }
      }
    },
    
    async toggleInnerDoor() {
      if (!this.canOperateInnerDoor && !this.innerDoorOpen) return
      
      const newValue = !this.innerDoorOpen
      
      if (await this.updateProperty('inner_door_open', newValue)) {
        this.innerDoorOpen = newValue
        if (this.innerDoorOpen) {
          this.addLog('info', 'Внутренняя дверь ОТКРЫТА')
        } else {
          this.addLog('info', 'Внутренняя дверь закрыта')
        }
      }
    },
    
    async startCycle() {
      if (!this.canStartCycle) return
      
      // Определяем направление цикла
      if (this.pressure >= 0.5) {
        this.cycleDirection = 'depressurize'
        this.addLog('info', 'Начата декомпрессия камеры шлюза')
        
        // Запускаем изменение давления через Celery
        if (await this.startPressureChange(0)) {
          this.cycleInProgress = true
          this.cycleProgress = 0
        }
      } else {
        this.cycleDirection = 'pressurize'
        this.addLog('info', 'Начата компрессия камеры шлюза')
        
        if (await this.startPressureChange(1)) {
          this.cycleInProgress = true
          this.cycleProgress = 0
        }
      }
    },
    
    completeCycle() {
      this.cycleInProgress = false
      this.cycleProgress = 100
      
      if (this.cycleDirection === 'depressurize') {
        this.addLog('success', 'Декомпрессия завершена. Давление: ВАКУУМ')
        this.addLog('info', 'Внешняя дверь готова к открытию')
      } else {
        this.addLog('success', 'Компрессия завершена. Давление: НОРМА')
        this.addLog('info', 'Внутренняя дверь готова к открытию')
      }
      
      this.cycleDirection = null
    },
    
    async abortCycle() {
      if (!this.cycleInProgress) return
      
      // Отменяем Celery задачу через API
      if (this.currentTaskId) {
        await this.cancelTask()
      }
      
      // Останавливаем polling задачи
      this.stopTaskPolling()
      
      this.cycleInProgress = false
      this.addLog('error', 'ЦИКЛ ПРЕРВАН! Давление нестабильно.')
      this.addLog('warning', 'Требуется повторный запуск цикла для стабилизации.')
      
      this.cycleDirection = null
      this.cycleProgress = 0
      this.currentTaskId = null
    },
    
    // ==================== UI методы ====================
    
    addLog(type, message) {
      const now = new Date()
      const time = now.toLocaleTimeString('ru-RU', { 
        hour: '2-digit', 
        minute: '2-digit', 
        second: '2-digit' 
      })
      
      this.log.unshift({ type, message, time })
      
      if (this.log.length > 20) {
        this.log.pop()
      }
    },
    
    generateBackgroundLines() {
      const lineCount = 12
      this.backgroundLines = []
      for (let i = 0; i < lineCount; i++) {
        this.backgroundLines.push({
          id: i,
          left: Math.random() * 100,
          duration: 10 + Math.random() * 15,
          delay: Math.random() * 8,
          opacity: 0.08 + Math.random() * 0.12
        })
      }
    }
  }
}
</script>

<style scoped>
.airlock {
  min-height: 100vh;
  background-color: #000000;
  display: flex;
  position: relative;
  overflow-x: hidden;
}

.content-wrapper {
  flex: 1;
  text-align: center;
  padding: 20px;
  max-width: 800px;
  margin: 0 auto;
  z-index: 1;
}

.title {
  color: #00ff00;
  font-family: 'Courier New', monospace;
  font-size: 28px;
  margin-bottom: 5px;
  text-shadow: 0 0 20px #00ff00;
  letter-spacing: 4px;
}

.subtitle {
  color: #00aa00;
  font-family: 'Courier New', monospace;
  font-size: 12px;
  margin-bottom: 5px;
  opacity: 0.7;
}

.object-info {
  color: #00aaff;
  font-family: 'Courier New', monospace;
  font-size: 11px;
  margin-bottom: 25px;
  opacity: 0.8;
}

/* Визуализация шлюза */
.airlock-visual {
  margin: 20px auto;
  max-width: 500px;
}

.airlock-chamber {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0;
  padding: 20px;
  border: 2px solid #00ff00;
  border-radius: 10px;
  background: linear-gradient(135deg, #001a00 0%, #002200 50%, #001a00 100%);
  transition: all 0.5s ease;
}

.airlock-chamber.chamber-cycling {
  animation: chamber-pulse 1s ease-in-out infinite;
  border-color: #ffaa00;
}

.airlock-chamber.chamber-vacuum {
  border-color: #ff0066;
  background: linear-gradient(135deg, #1a0011 0%, #220011 50%, #1a0011 100%);
}

.airlock-chamber.chamber-disconnected {
  border-color: #666666;
  background: linear-gradient(135deg, #1a1a1a 0%, #222222 50%, #1a1a1a 100%);
  opacity: 0.7;
}

@keyframes chamber-pulse {
  0%, 100% { box-shadow: 0 0 10px #ffaa00; }
  50% { box-shadow: 0 0 30px #ffaa00; }
}

.door {
  width: 60px;
  height: 120px;
  border: 2px solid #00ff00;
  background: linear-gradient(180deg, #003300 0%, #001a00 100%);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  transition: all 0.5s ease;
  position: relative;
}

.door-outer {
  border-radius: 10px 0 0 10px;
}

.door-inner {
  border-radius: 0 10px 10px 0;
}

.door.door-open {
  background: linear-gradient(180deg, #004400 0%, #002200 100%);
  border-color: #00ffaa;
  box-shadow: 0 0 15px #00ffaa;
}

.door-label {
  font-family: 'Courier New', monospace;
  font-size: 10px;
  color: #00ff00;
  margin-bottom: 10px;
}

.door-indicator {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  border: 2px solid #00ff00;
}

.indicator-closed {
  background: #ff0000;
  box-shadow: 0 0 10px #ff0000;
}

.indicator-open {
  background: #00ff00;
  box-shadow: 0 0 10px #00ff00;
}

.chamber-body {
  width: 200px;
  height: 120px;
  border-top: 2px solid #00ff00;
  border-bottom: 2px solid #00ff00;
  background: linear-gradient(180deg, #002200 0%, #001100 50%, #002200 100%);
  display: flex;
  align-items: center;
  justify-content: center;
}

.chamber-content {
  text-align: center;
}

.pressure-gauge {
  margin-bottom: 10px;
}

.gauge-label {
  font-family: 'Courier New', monospace;
  font-size: 10px;
  color: #00aa00;
}

.gauge-value {
  font-family: 'Courier New', monospace;
  font-size: 18px;
  color: #00ff00;
  text-shadow: 0 0 10px #00ff00;
}

.gauge-bar {
  width: 150px;
  height: 8px;
  border: 1px solid #00ff00;
  border-radius: 4px;
  margin: 5px auto;
  overflow: hidden;
}

.gauge-fill {
  height: 100%;
  background: linear-gradient(90deg, #ff0066, #ffaa00, #00ff00);
  transition: width 0.1s linear;
}

.cycle-indicator {
  margin-top: 5px;
}

.cycle-label {
  font-family: 'Courier New', monospace;
  font-size: 10px;
  color: #ffaa00;
  animation: blink 0.5s ease-in-out infinite;
}

.cycle-progress {
  font-family: 'Courier New', monospace;
  font-size: 24px;
  color: #ffaa00;
  text-shadow: 0 0 15px #ffaa00;
}

.progress-bar {
  width: 150px;
  height: 6px;
  border: 1px solid #ffaa00;
  border-radius: 3px;
  margin: 5px auto;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #ffaa00, #ff6600);
  transition: width 0.1s linear;
}

@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

/* Статус панель */
.status-panel {
  margin: 20px auto;
  max-width: 400px;
  border: 1px solid #00ff00;
  border-radius: 8px;
  padding: 15px;
  background: rgba(0, 50, 0, 0.3);
}

.status-row {
  display: flex;
  justify-content: space-between;
  margin: 8px 0;
  font-family: 'Courier New', monospace;
  font-size: 14px;
}

.status-label {
  color: #00aa00;
}

.status-value {
  font-weight: bold;
}

.status-ready { color: #00ff00; }
.status-locked { color: #ff6600; }
.status-unlocked { color: #00ff00; }
.status-warning { color: #ffaa00; }
.status-cycling { color: #ffaa00; animation: blink 0.5s ease-in-out infinite; }
.status-vacuum { color: #ff0066; }
.status-open { color: #00ffaa; }
.status-closed { color: #888888; }
.status-error { color: #ff0066; animation: blink 0.5s ease-in-out infinite; }
.status-loading { color: #00aaff; animation: blink 1s ease-in-out infinite; }

/* Панель управления */
.control-panel {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 15px;
  margin: 20px auto;
  max-width: 500px;
}

.control-section {
  border: 1px solid #004400;
  border-radius: 8px;
  padding: 15px;
  background: rgba(0, 30, 0, 0.5);
}

.control-section h3 {
  color: #00aa00;
  font-family: 'Courier New', monospace;
  font-size: 12px;
  margin-bottom: 10px;
  letter-spacing: 2px;
}

.cycle-section {
  grid-column: span 2;
}

.control-btn {
  width: 100%;
  padding: 12px 20px;
  font-family: 'Courier New', monospace;
  font-size: 14px;
  border: 2px solid;
  border-radius: 5px;
  cursor: pointer;
  transition: all 0.3s;
  background: transparent;
  margin: 5px 0;
}

.control-btn:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}

.btn-lock {
  border-color: #ff6600;
  color: #ff6600;
}

.btn-lock:hover:not(:disabled) {
  background: #ff6600;
  color: #000;
}

.btn-unlock {
  border-color: #00ff00;
  color: #00ff00;
}

.btn-unlock:hover:not(:disabled) {
  background: #00ff00;
  color: #000;
}

.btn-open {
  border-color: #00ffaa;
  color: #00ffaa;
}

.btn-open:hover:not(:disabled) {
  background: #00ffaa;
  color: #000;
}

.btn-close {
  border-color: #ffaa00;
  color: #ffaa00;
}

.btn-close:hover:not(:disabled) {
  background: #ffaa00;
  color: #000;
}

.btn-cycle {
  border-color: #00aaff;
  color: #00aaff;
}

.btn-cycle:hover:not(:disabled) {
  background: #00aaff;
  color: #000;
}

.btn-abort {
  border-color: #ff0066;
  color: #ff0066;
}

.btn-abort:hover:not(:disabled) {
  background: #ff0066;
  color: #000;
}

/* Лог панель */
.log-panel {
  margin: 20px auto;
  max-width: 500px;
  border: 1px solid #004400;
  border-radius: 8px;
  padding: 15px;
  background: rgba(0, 20, 0, 0.5);
}

.log-panel h3 {
  color: #00aa00;
  font-family: 'Courier New', monospace;
  font-size: 12px;
  margin-bottom: 10px;
  letter-spacing: 2px;
  text-align: left;
}

.log-content {
  max-height: 150px;
  overflow-y: auto;
  text-align: left;
}

.log-entry {
  font-family: 'Courier New', monospace;
  font-size: 11px;
  padding: 3px 0;
  border-bottom: 1px solid #002200;
}

.log-time {
  color: #006600;
  margin-right: 10px;
}

.log-info .log-message { color: #00ff00; }
.log-warning .log-message { color: #ffaa00; }
.log-error .log-message { color: #ff0066; }
.log-success .log-message { color: #00ffaa; }

/* Справка */
.help-section {
  margin-top: 20px;
  padding: 15px;
  border: 1px solid #004400;
  border-radius: 8px;
  background-color: rgba(0, 50, 0, 0.3);
  max-width: 400px;
  margin-left: auto;
  margin-right: auto;
}

.help-section h3 {
  color: #00ff00;
  font-size: 14px;
  margin-bottom: 10px;
  font-family: 'Courier New', monospace;
}

.help-section p {
  color: #00cc00;
  font-size: 12px;
  margin: 5px 0;
  font-family: 'Courier New', monospace;
  text-align: left;
}

.help-section .warning {
  color: #ff6600;
  margin-top: 10px;
}

.help-section.task-info {
  border-color: #00aaff;
  background-color: rgba(0, 100, 150, 0.2);
}

.help-section.task-info h3 {
  color: #00aaff;
}

.help-section.creating-info {
  border-color: #ffaa00;
  background-color: rgba(150, 100, 0, 0.2);
  animation: pulse-creating 1s ease-in-out infinite;
}

.help-section.creating-info h3 {
  color: #ffaa00;
}

@keyframes pulse-creating {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.6; }
}

.url-hint {
  margin-top: 10px;
  color: #00aaff;
}

.url-example {
  font-family: 'Courier New', monospace;
  background-color: rgba(0, 100, 150, 0.3);
  padding: 5px 10px;
  border-radius: 4px;
  color: #00ffff;
  margin-top: 5px;
}

/* Фоновые линии */
.background-lines {
  position: fixed;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
  z-index: 0;
  overflow: hidden;
}

.bg-line {
  position: absolute;
  top: -100%;
  width: 1px;
  height: 200%;
  background: linear-gradient(
    to bottom,
    transparent 0%,
    #00ff00 20%,
    #00ff00 80%,
    transparent 100%
  );
  animation: line-scroll linear infinite;
}

@keyframes line-scroll {
  0% { transform: translateY(0); }
  100% { transform: translateY(100%); }
}

/* Мобильная версия */
@media (max-width: 768px) {
  .content-wrapper {
    padding: 10px;
  }

  .title {
    font-size: 20px;
    letter-spacing: 2px;
  }

  .control-panel {
    grid-template-columns: 1fr;
  }

  .cycle-section {
    grid-column: span 1;
  }

  .airlock-chamber {
    flex-direction: column;
    padding: 15px;
  }

  .door {
    width: 100px;
    height: 50px;
    flex-direction: row;
    gap: 10px;
  }

  .door-outer {
    border-radius: 10px 10px 0 0;
  }

  .door-inner {
    border-radius: 0 0 10px 10px;
  }

  .chamber-body {
    width: 100px;
    height: 150px;
    border-left: 2px solid #00ff00;
    border-right: 2px solid #00ff00;
    border-top: none;
    border-bottom: none;
  }

  .gauge-bar,
  .progress-bar {
    width: 80px;
  }

  .status-row {
    font-size: 12px;
  }

  .control-btn {
    font-size: 12px;
    padding: 10px 15px;
  }
}
</style>
