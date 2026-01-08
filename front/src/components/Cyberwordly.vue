<template>
  <div class="cyberwordly">
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
        <p>Код взломан: {{ secretCode.join('') }}</p>
        <a href="#" @click.prevent="reloadPage" class="reload-link">[ НАЧАТЬ ЗАНОВО ]</a>
      </div>

      <!-- Game over message -->
      <div v-if="gameLost" class="gameover-message">
        <h2>❌ ДОСТУП ЗАПРЕЩЕН ❌</h2>
        <p>Код был: {{ secretCode.join('') }}</p>
        <a href="#" @click.prevent="reloadPage" class="reload-link">[ ПОПРОБОВАТЬ СНОВА ]</a>
      </div>

      <table class="data-table">
        <tr v-for="(row, rowIndex) in tableData" :key="rowIndex">
          <td 
            v-for="(cell, colIndex) in row" 
            :key="colIndex"
            :class="getCellClass(rowIndex, colIndex)"
          >
            {{ cell.value }}
          </td>
        </tr>
      </table>
      <table class="input-table" v-if="!gameWon && !gameLost">
        <tr v-for="(row, rowIndex) in inputTableData" :key="rowIndex">
          <td v-for="(cell, colIndex) in row" :key="colIndex" @click="fillDataTable(cell)">
            {{ cell }}
          </td>
        </tr>
      </table>

      <div class="help-section">
        <h3>Справка</h3>
        <p>Угадайте 5-символьный код за {{ maxAttempts }} попыток.</p>
        <p>🟢 Зелёный — символ на своём месте</p>
        <p>🟡 Жёлтый — символ есть, но не там</p>
        <p>🔴 Красный — символа нет в коде</p>
      </div>
    </div>
  </div>
</template>

<script>
export default {
  name: 'CyberwordlyPage',
  data() {
    return {
      tableData: [],
      inputTableData: [
        ['0', '1', '2', '3', '4', '5', '6', '7'],
        ['8', '9', 'A', 'B', 'C', 'D', 'E', 'F'],
      ],
      backgroundLines: [],
      currentRow: 0,
      currentCol: 0,
      secretCode: [],
      gameWon: false,
      gameLost: false,
      maxAttempts: 6
    }
  },
  created() {
    this.initializeTable()
    this.generateSecretCode()
  },
  mounted() {
    document.title = 'Cyberwordly'
    this.generateBackgroundLines()
  },
  methods: {
    initializeTable() {
      // Создаём таблицу 6x5 с объектами {value, status}
      this.tableData = []
      for (let i = 0; i < this.maxAttempts; i++) {
        const row = []
        for (let j = 0; j < 5; j++) {
          row.push({ value: '', status: 'empty' })
        }
        this.tableData.push(row)
      }
    },
    generateSecretCode() {
      // Генерируем код из 5 уникальных символов (0-9, A-F)
      const allChars = ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9', 'A', 'B', 'C', 'D', 'E', 'F']
      const shuffled = [...allChars].sort(() => Math.random() - 0.5)
      this.secretCode = shuffled.slice(0, 5)
      console.log('Secret code:', this.secretCode.join('')) // Для отладки
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
    },
    fillDataTable(value) {
      if (this.gameWon || this.gameLost) return
      if (this.currentRow >= this.maxAttempts) return // Все попытки исчерпаны

      // Заполняем текущую ячейку
      this.tableData[this.currentRow][this.currentCol].value = value
      this.currentCol++

      // Если заполнили всю строку (5 символов)
      if (this.currentCol >= 5) {
        this.evaluateRow(this.currentRow)
        this.currentCol = 0
        this.currentRow++
      }
    },
    evaluateRow(rowIndex) {
      const row = this.tableData[rowIndex]
      const guess = row.map(cell => cell.value)

      // Проверяем каждый символ
      for (let i = 0; i < 5; i++) {
        const char = guess[i]
        if (char === this.secretCode[i]) {
          // Символ на своём месте - зелёный
          row[i].status = 'correct'
        } else if (this.secretCode.includes(char)) {
          // Символ есть в коде, но не на своём месте - жёлтый
          row[i].status = 'wrong-position'
        } else {
          // Символа нет в коде - красный
          row[i].status = 'not-in-code'
        }
      }

      // Проверяем, угадан ли код
      const allCorrect = row.every(cell => cell.status === 'correct')
      if (allCorrect) {
        this.gameWon = true
      } else if (rowIndex >= this.maxAttempts - 1) {
        // Это была последняя попытка
        this.gameLost = true
      }
    },
    getCellClass(rowIndex, colIndex) {
      const cell = this.tableData[rowIndex][colIndex]
      return {
        'cell-correct': cell.status === 'correct',
        'cell-wrong-position': cell.status === 'wrong-position',
        'cell-not-in-code': cell.status === 'not-in-code'
      }
    },
    reloadPage() {
      window.location.reload()
    }
  }
}
</script>

<style scoped>
.cyberwordly {
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

h1 {
  color: #42b983;
  margin-bottom: 20px;
}

p {
  color: #00ff00;
  font-size: 18px;
}

.data-table {
  margin: 20px auto;
  border-collapse: collapse;
  background-color: #000000;
}

.data-table td {
  border: 1px solid #00ff00;
  padding: 15px;
  color: #00ff00;
  background-color: #000000;
  width: 100px;
  height: 100px;
  text-align: center;
  font-size: 24px;
  font-weight: bold;
  transition: background-color 0.3s, border-color 0.3s;
}

/* Зелёный - символ на своём месте */
.data-table td.cell-correct {
  background-color: #006600;
  border-color: #00ff00;
  color: #00ff00;
  text-shadow: 0 0 10px #00ff00;
}

/* Жёлтый - символ есть, но не на своём месте */
.data-table td.cell-wrong-position {
  background-color: #666600;
  border-color: #ffff00;
  color: #ffff00;
  text-shadow: 0 0 10px #ffff00;
}

/* Красный - символа нет в коде */
.data-table td.cell-not-in-code {
  background-color: #660000;
  border-color: #ff0000;
  color: #ff0000;
  text-shadow: 0 0 10px #ff0000;
}

.input-table {
  margin: 20px auto;
  border-collapse: collapse;
  background-color: #000000;
}

.input-table td {
  border: 1px solid #00ff00;
  padding: 15px;
  color: #00ff00;
  background-color: #000000;
  width: 80px;
  height: 80px;
  text-align: center;
  font-size: 24px;
  font-weight: bold;
  cursor: pointer;
  transition: background-color 0.2s;
}

.input-table td:hover {
  background-color: #003300;
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

  .data-table td {
    width: 50px;
    height: 50px;
    padding: 8px;
    font-size: 18px;
  }

  .input-table td {
    width: 40px;
    height: 40px;
    padding: 8px;
    font-size: 16px;
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
}
</style>
