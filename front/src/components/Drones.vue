<template>
  <div class="drones">
    <!-- Фоновые вертикальные линии -->
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
      <!-- Victory message -->
      <div v-if="gameWon" class="victory-message">
        <h2>🎉 ДОСТУП РАЗРЕШЕН 🎉</h2>
        <p>Зона успешно пройдена</p>
        <a href="#" @click.prevent="reloadPage" class="reload-link">[ НАЧАТЬ ЗАНОВО ]</a>
      </div>

      <!-- Game over message -->
      <div v-if="gameLost" class="gameover-message">
        <h2>❌ ДОСТУП ЗАПРЕЩЕН ❌</h2>
        <p>Обнаружено дронов: {{ droneHits }}</p>
        <a href="#" @click.prevent="reloadPage" class="reload-link">[ ПОПРОБОВАТЬ СНОВА ]</a>
      </div>

      <!-- Drone hit message -->
      <div v-if="droneHitMessage && !gameLost" class="drone-hit-message">
        <p>⚠️ ОБНАРУЖЕН ДРОН! Попытка {{ droneHits }}/{{ maxDroneHits }}</p>
      </div>

      <!-- Status bar -->
      <div class="status-bar" v-if="!gameLost && !gameWon">
        <span>ПОПЫТКИ: {{ droneHits }}/{{ maxDroneHits }}</span>
        <span>ЦЕЛЬ: достичь 6-й строки</span>
      </div>

      <table class="game-table">
        <tr v-for="(row, rowIndex) in grid" :key="rowIndex">
          <td 
            v-for="(cell, colIndex) in row" 
            :key="colIndex"
            :class="getCellClass(rowIndex, colIndex)"
            @click="openCell(rowIndex, colIndex)"
          >
            <span v-if="cell.isOpen">
              <span v-if="cell.isDrone">✈</span>
              <span v-else-if="cell.adjacentDrones > 0">{{ cell.adjacentDrones }}</span>
              <span v-else>&nbsp;</span>
            </span>
            <span v-else class="hidden-cell">?</span>
          </td>
        </tr>
      </table>

      <div class="hint-text" v-if="!gameWon && !gameLost">
        <p v-if="!firstMoveMade">Выберите любую клетку в первой строке для начала</p>
        <p v-else>Открывайте клетки рядом с последней открытой</p>
      </div>

      <div class="help-section">
        <h3>Справка</h3>
        <p>Доберитесь от первой до последней строки.</p>
        <p>✈ — дрон (сбрасывает позицию)</p>
        <p>Цифра — количество дронов рядом</p>
        <p>Ходы только по вертикали и горизонтали</p>
        <p>Попыток: {{ maxDroneHits }}</p>
      </div>
    </div>
  </div>
</template>

<script>
export default {
  name: 'DronesPage',
  data() {
    return {
      grid: [],
      gridSize: 6,
      droneCount: 12,
      firstMoveMade: false,
      lastOpenedCell: null,
      gameWon: false,
      gameLost: false,
      droneHitMessage: false,
      droneHits: 0,
      maxDroneHits: 5,
      backgroundLines: []
    }
  },
  created() {
    this.initializeGrid()
  },
  mounted() {
    document.title = 'Drones'
    this.generateBackgroundLines()
  },
  methods: {
    initializeGrid() {
      // Создаём пустую сетку
      this.grid = []
      for (let i = 0; i < this.gridSize; i++) {
        const row = []
        for (let j = 0; j < this.gridSize; j++) {
          row.push({
            isDrone: false,
            isOpen: false,
            adjacentDrones: 0
          })
        }
        this.grid.push(row)
      }

      // Сначала генерируем гарантированный путь от первой до последней строки
      const safePath = this.generateSafePath()

      // Сначала гарантируем, что в каждой колонке есть хотя бы один дрон
      let dronesPlaced = 0
      const maxDronesInFirstRow = 1
      const maxDronesInLastRow = 3
      const maxDronesPerRow = 3
      const lastRowIndex = this.gridSize - 1
      const dronesPerColumn = new Array(this.gridSize).fill(0)
      const dronesPerRow = new Array(this.gridSize).fill(0)
      
      // Размещаем по одному дрону в каждую колонку
      for (let col = 0; col < this.gridSize; col++) {
        // Ищем случайную строку для дрона в этой колонке
        const availableRows = []
        for (let row = 0; row < this.gridSize; row++) {
          const key = `${row},${col}`
          // Пропускаем первую строку если уже есть дрон там
          if (row === 0 && dronesPerRow[0] >= maxDronesInFirstRow) continue
          // Пропускаем последнюю строку если достигнут лимит
          if (row === lastRowIndex && dronesPerRow[lastRowIndex] >= maxDronesInLastRow) continue
          // Пропускаем строку если достигнут общий лимит
          if (dronesPerRow[row] >= maxDronesPerRow) continue
          if (!safePath.has(key)) {
            availableRows.push(row)
          }
        }
        
        if (availableRows.length > 0) {
          const randomRow = availableRows[Math.floor(Math.random() * availableRows.length)]
          this.grid[randomRow][col].isDrone = true
          dronesPlaced++
          dronesPerColumn[col]++
          dronesPerRow[randomRow]++
        }
      }
      
      // Добавляем оставшиеся дроны случайным образом
      const maxDrones = Math.min(this.droneCount, this.gridSize * this.gridSize - safePath.size)
      
      while (dronesPlaced < maxDrones) {
        const row = Math.floor(Math.random() * this.gridSize)
        const col = Math.floor(Math.random() * this.gridSize)
        const key = `${row},${col}`
        
        // Проверяем лимит дронов в первой строке
        if (row === 0 && dronesPerRow[0] >= maxDronesInFirstRow) {
          continue
        }
        
        // Проверяем лимит дронов в последней строке
        if (row === lastRowIndex && dronesPerRow[lastRowIndex] >= maxDronesInLastRow) {
          continue
        }
        
        // Проверяем общий лимит дронов в строке
        if (dronesPerRow[row] >= maxDronesPerRow) {
          continue
        }
        
        if (!this.grid[row][col].isDrone && !safePath.has(key)) {
          this.grid[row][col].isDrone = true
          dronesPlaced++
          dronesPerRow[row]++
        }
      }

      // Вычисляем количество соседних дронов для каждой клетки
      for (let i = 0; i < this.gridSize; i++) {
        for (let j = 0; j < this.gridSize; j++) {
          if (!this.grid[i][j].isDrone) {
            this.grid[i][j].adjacentDrones = this.countAdjacentDrones(i, j)
          }
        }
      }

      // Выводим схему расположения дронов в консоль
      this.logDroneLayout(safePath)
    },
    logDroneLayout(safePath) {
      console.log('=== СХЕМА РАСПОЛОЖЕНИЯ ДРОНОВ ===')
      console.log('D = дрон, . = пусто, * = безопасный путь')
      console.log('')
      
      let output = '  '
      for (let j = 0; j < this.gridSize; j++) {
        output += j + ' '
      }
      console.log(output)
      
      for (let i = 0; i < this.gridSize; i++) {
        let rowStr = i + ' '
        for (let j = 0; j < this.gridSize; j++) {
          const key = `${i},${j}`
          if (this.grid[i][j].isDrone) {
            rowStr += 'D '
          } else if (safePath.has(key)) {
            rowStr += '* '
          } else {
            rowStr += '. '
          }
        }
        console.log(rowStr)
      }
      console.log('')
      console.log('Всего дронов:', this.droneCount)
      console.log('Размер безопасного пути:', safePath.size)
      console.log('=================================')
    },
    generateSafePath() {
      // Генерируем случайный путь от первой строки до последней
      // Путь идёт только по горизонтали и вертикали (без диагоналей)
      // Путь не должен превышать 9 клеток
      // Не больше 3 клеток подряд по вертикали в одной колонке
      // Не возвращаемся на уже посещённые клетки
      const maxPathSize = 9
      const maxVerticalInRow = 3
      const visitedCells = new Set()
      
      // Начинаем с случайной колонки в первой строке
      let currentRow = 0
      let currentCol = Math.floor(Math.random() * this.gridSize)
      let consecutiveVertical = 1
      visitedCells.add(`${currentRow},${currentCol}`)
      
      // Строим путь до последней строки
      while (currentRow < this.gridSize - 1) {
        // Проверяем лимит размера пути
        if (visitedCells.size >= maxPathSize) {
          // Если достигли лимита, идём напрямую вниз
          currentRow++
          visitedCells.add(`${currentRow},${currentCol}`)
          continue
        }
        
        // Нужно ли обязательно идти горизонтально?
        const mustGoHorizontal = consecutiveVertical >= maxVerticalInRow
        
        // Определяем доступные направления (не посещённые клетки)
        const downKey = `${currentRow + 1},${currentCol}`
        const leftKey = `${currentRow},${currentCol - 1}`
        const rightKey = `${currentRow},${currentCol + 1}`
        
        const canGoDown = currentRow < this.gridSize - 1 && !visitedCells.has(downKey)
        const canGoLeft = currentCol > 0 && !visitedCells.has(leftKey)
        const canGoRight = currentCol < this.gridSize - 1 && !visitedCells.has(rightKey)
        
        if (mustGoHorizontal && (canGoLeft || canGoRight)) {
          // Обязательно идём влево или вправо
          if (canGoLeft && canGoRight) {
            currentCol += Math.random() < 0.5 ? -1 : 1
          } else if (canGoLeft) {
            currentCol--
          } else {
            currentCol++
          }
          consecutiveVertical = 0
          visitedCells.add(`${currentRow},${currentCol}`)
        } else if (canGoDown || canGoLeft || canGoRight) {
          // Выбираем случайно из доступных направлений
          const options = []
          if (canGoDown && consecutiveVertical < maxVerticalInRow) options.push('down', 'down', 'down') // 75% вниз
          if (canGoLeft) options.push('left')
          if (canGoRight) options.push('right')
          
          // Если нет вариантов кроме вниз
          if (options.length === 0 && canGoDown) {
            options.push('down')
          }
          
          if (options.length > 0) {
            const choice = options[Math.floor(Math.random() * options.length)]
            
            if (choice === 'down') {
              currentRow++
              consecutiveVertical++
            } else if (choice === 'left') {
              currentCol--
              consecutiveVertical = 0
            } else if (choice === 'right') {
              currentCol++
              consecutiveVertical = 0
            }
            visitedCells.add(`${currentRow},${currentCol}`)
          } else {
            // Нет доступных ходов - принудительно вниз
            currentRow++
            consecutiveVertical++
            visitedCells.add(`${currentRow},${currentCol}`)
          }
        } else {
          // Нет доступных ходов - принудительно вниз (игнорируем посещённость)
          currentRow++
          consecutiveVertical++
          visitedCells.add(`${currentRow},${currentCol}`)
        }
      }
      
      return visitedCells
    },
    countAdjacentDrones(row, col) {
      let count = 0
      for (let di = -1; di <= 1; di++) {
        for (let dj = -1; dj <= 1; dj++) {
          if (di === 0 && dj === 0) continue
          const ni = row + di
          const nj = col + dj
          if (ni >= 0 && ni < this.gridSize && nj >= 0 && nj < this.gridSize) {
            if (this.grid[ni][nj].isDrone) {
              count++
            }
          }
        }
      }
      return count
    },
    canInteractCell(row, col) {
      // Если игра выиграна или проиграна - нельзя взаимодействовать
      if (this.gameWon || this.gameLost) return false

      // Первый ход - только первая строка (можно кликать и на открытые клетки)
      if (!this.firstMoveMade) {
        return row === 0
      }

      // После первого хода - только рядом с последней открытой клеткой (по горизонтали/вертикали)
      return this.isAdjacentToLastOpened(row, col)
    },
    isAdjacentToLastOpened(row, col) {
      // Проверяем соседство только с последней открытой клеткой
      if (!this.lastOpenedCell) return false
      
      const lastRow = this.lastOpenedCell.row
      const lastCol = this.lastOpenedCell.col
      
      // Клетка соседняя только по горизонтали или вертикали (не по диагонали)
      const rowDiff = Math.abs(row - lastRow)
      const colDiff = Math.abs(col - lastCol)
      
      // Соседняя по горизонтали (та же строка, соседняя колонка) или по вертикали (соседняя строка, та же колонка)
      return (rowDiff === 1 && colDiff === 0) || (rowDiff === 0 && colDiff === 1)
    },
    openCell(row, col) {
      if (!this.canInteractCell(row, col)) return

      const cell = this.grid[row][col]

      // Если клетка уже открыта и безопасна - просто перемещаем позицию
      if (cell.isOpen && !cell.isDrone) {
        this.firstMoveMade = true
        this.lastOpenedCell = { row, col }
        return
      }

      // Открываем клетку
      cell.isOpen = true

      if (cell.isDrone) {
        // Попали на дрон - увеличиваем счётчик и сбрасываем позицию
        this.droneHits++
        this.droneHitMessage = true
        this.firstMoveMade = false
        this.lastOpenedCell = null
        
        // Проверяем проигрыш
        if (this.droneHits >= this.maxDroneHits) {
          this.gameLost = true
          this.droneHitMessage = false
        } else {
          // Скрываем сообщение через 2 секунды
          setTimeout(() => {
            this.droneHitMessage = false
          }, 2000)
        }
      } else {
        this.firstMoveMade = true
        this.lastOpenedCell = { row, col }
        
        // Проверяем победу
        this.checkWin(row)
      }
    },
    checkWin(row) {
      // Победа, если достигнута последняя (6-я) строка
      if (row === this.gridSize - 1) {
        this.gameWon = true
      }
    },
    getCellClass(row, col) {
      const cell = this.grid[row][col]
      const canInteract = this.canInteractCell(row, col)
      
      return {
        'cell-open': cell.isOpen && !cell.isDrone,
        'cell-drone': cell.isOpen && cell.isDrone,
        'cell-closed': !cell.isOpen,
        'cell-available': canInteract,
        'cell-locked': !canInteract && !cell.isOpen
      }
    },
    reloadPage() {
      window.location.reload()
    },
    generateBackgroundLines() {
      // Генерируем вертикальные линии для фона
      const lineCount = 15
      this.backgroundLines = []
      for (let i = 0; i < lineCount; i++) {
        this.backgroundLines.push({
          id: i,
          left: Math.random() * 100,
          duration: 8 + Math.random() * 12,
          delay: Math.random() * 10,
          opacity: 0.1 + Math.random() * 0.2
        })
      }
    }
  }
}
</script>

<style scoped>
.drones {
  min-height: 100vh;
  background-color: #000000;
  display: flex;
  position: relative;
  overflow: hidden;
}

.content-wrapper {
  flex: 1;
  text-align: center;
  padding: 20px;
  max-width: 600px;
  margin: 0 auto;
  z-index: 1;
}

.status-bar {
  display: flex;
  justify-content: center;
  gap: 40px;
  margin-bottom: 20px;
  font-family: 'Courier New', monospace;
  font-size: 18px;
  color: #00ff00;
}

.game-table {
  margin: 20px auto;
  border-collapse: collapse;
  background-color: #000000;
}

.game-table td {
  border: 1px solid #00ff00;
  padding: 0;
  width: 80px;
  height: 80px;
  text-align: center;
  font-size: 24px;
  font-weight: bold;
  cursor: pointer;
  transition: all 0.3s;
  font-family: 'Courier New', monospace;
}

/* Закрытая клетка */
.game-table td.cell-closed {
  background-color: #001a00;
  color: #004400;
}

/* Доступная для открытия клетка */
.game-table td.cell-available {
  background-color: #002200;
  color: #00ff00;
  border-color: #00ff00;
  animation: pulse-available 1.5s ease-in-out infinite;
}

.game-table td.cell-available:hover {
  background-color: #004400;
  box-shadow: 0 0 15px #00ff00;
}

/* Заблокированная клетка */
.game-table td.cell-locked {
  background-color: #0a0a0a;
  color: #333333;
  border-color: #333333;
  cursor: not-allowed;
}

/* Открытая безопасная клетка */
.game-table td.cell-open {
  background-color: #003300;
  color: #00ff00;
  border-color: #00ff00;
  text-shadow: 0 0 10px #00ff00;
}

/* Клетка с дроном */
.game-table td.cell-drone {
  background-color: #660000;
  color: #ff0000;
  border-color: #ff0000;
  text-shadow: 0 0 10px #ff0000;
  animation: drone-pulse 0.5s ease-in-out;
}

.hidden-cell {
  opacity: 0.5;
}

@keyframes pulse-available {
  0%, 100% {
    box-shadow: 0 0 5px #00ff00;
  }
  50% {
    box-shadow: 0 0 15px #00ff00, inset 0 0 10px rgba(0, 255, 0, 0.1);
  }
}

@keyframes drone-pulse {
  0% {
    transform: scale(1);
  }
  50% {
    transform: scale(1.1);
  }
  100% {
    transform: scale(1);
  }
}

.hint-text {
  margin-top: 20px;
  font-family: 'Courier New', monospace;
  font-size: 14px;
  color: #00ff00;
  opacity: 0.7;
}

.victory-message {
  background: linear-gradient(135deg, #001a00 0%, #003300 50%, #001a00 100%);
  border: 2px solid #00ff00;
  border-radius: 10px;
  padding: 30px;
  margin: 20px auto;
  max-width: 400px;
  animation: victory-glow 1s ease-in-out infinite alternate;
}

.victory-message h2 {
  color: #00ff00;
  font-size: 28px;
  margin-bottom: 15px;
  text-shadow: 0 0 20px #00ff00;
}

.victory-message p {
  color: #00ff00;
  font-size: 20px;
  margin-bottom: 20px;
  font-family: 'Courier New', monospace;
}

.reload-link {
  display: inline-block;
  color: #00ffff;
  font-size: 18px;
  text-decoration: none;
  padding: 10px 20px;
  border: 1px solid #00ffff;
  transition: all 0.3s;
  font-family: 'Courier New', monospace;
}

.reload-link:hover {
  background-color: #00ffff;
  color: #000000;
  text-shadow: none;
}

@keyframes victory-glow {
  from {
    box-shadow: 0 0 20px #00ff00, inset 0 0 20px rgba(0, 255, 0, 0.1);
  }
  to {
    box-shadow: 0 0 40px #00ff00, inset 0 0 30px rgba(0, 255, 0, 0.2);
  }
}

.gameover-message {
  background: linear-gradient(135deg, #1a0000 0%, #330000 50%, #1a0000 100%);
  border: 2px solid #ff0000;
  border-radius: 10px;
  padding: 30px;
  margin: 20px auto;
  max-width: 400px;
  animation: gameover-glow 1s ease-in-out infinite alternate;
}

.gameover-message h2 {
  color: #ff0000;
  font-size: 28px;
  margin-bottom: 15px;
  text-shadow: 0 0 20px #ff0000;
}

.gameover-message p {
  color: #ff0000;
  font-size: 20px;
  margin-bottom: 20px;
  font-family: 'Courier New', monospace;
}

.gameover-message .reload-link {
  border-color: #ff6600;
  color: #ff6600;
}

.gameover-message .reload-link:hover {
  background-color: #ff6600;
  color: #000000;
}

@keyframes gameover-glow {
  from {
    box-shadow: 0 0 20px #ff0000, inset 0 0 20px rgba(255, 0, 0, 0.1);
  }
  to {
    box-shadow: 0 0 40px #ff0000, inset 0 0 30px rgba(255, 0, 0, 0.2);
  }
}

.drone-hit-message {
  background: linear-gradient(135deg, #1a0000 0%, #330000 50%, #1a0000 100%);
  border: 2px solid #ff0000;
  border-radius: 10px;
  padding: 15px;
  margin: 10px auto;
  max-width: 400px;
  animation: drone-hit-flash 0.3s ease-in-out 3;
}

.drone-hit-message p {
  color: #ff0000;
  font-size: 16px;
  margin: 0;
  font-family: 'Courier New', monospace;
  text-shadow: 0 0 10px #ff0000;
}

@keyframes drone-hit-flash {
  0%, 100% {
    opacity: 1;
  }
  50% {
    opacity: 0.5;
  }
}

/* Фоновые вертикальные линии */
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
  width: 2px;
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
  0% {
    transform: translateY(0);
  }
  100% {
    transform: translateY(100%);
  }
}

.help-section {
  margin-top: 30px;
  padding: 15px;
  border: 1px solid #004400;
  border-radius: 8px;
  background-color: rgba(0, 50, 0, 0.3);
  max-width: 350px;
  margin-left: auto;
  margin-right: auto;
}

.help-section h3 {
  color: #00ff00;
  font-size: 16px;
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

/* Мобильная версия */
@media (max-width: 768px) {
  .content-wrapper {
    padding: 10px;
  }

  .game-table td {
    width: 45px;
    height: 45px;
    font-size: 18px;
  }

  .status-bar {
    font-size: 14px;
    gap: 20px;
  }

  .victory-message,
  .gameover-message {
    padding: 20px;
    margin: 10px;
  }

  .victory-message h2,
  .gameover-message h2 {
    font-size: 20px;
  }

  .victory-message p,
  .gameover-message p {
    font-size: 16px;
  }

  .help-section {
    max-width: 100%;
  }

  .hint-text {
    font-size: 12px;
  }
}
</style>

