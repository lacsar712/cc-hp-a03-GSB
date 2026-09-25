<script>
  let username = 'processor'
  let password = 'herb123456'
  let token = localStorage.getItem('herb_token') || ''
  let role = localStorage.getItem('herb_role') || ''
  let me = localStorage.getItem('herb_username') || ''
  let view = 'batches'

  let rows = []
  let pots = []
  let events = []

  let herb = '黄芩'
  let potId = ''
  let tempC = 120
  let minutes = 12
  let error = ''

  let newPotName = '甲锅'
  let newCertNo = ''
  let certMsg = ''
  let editingId = null
  let editingName = ''

  async function api(path, options = {}) {
    const res = await fetch(path, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || '请求失败')
    return data
  }

  async function enter() {
    error = ''
    try {
      const data = await api('/api/auth/login', {
        method: 'POST',
        body: JSON.stringify({ username, password }),
      })
      token = data.access_token
      role = data.role
      me = data.username
      localStorage.setItem('herb_token', token)
      localStorage.setItem('herb_role', role)
      localStorage.setItem('herb_username', me)
      await loadAll()
    } catch (err) {
      error = err.message
    }
  }

  async function loadAll() {
    ;[rows, pots, events] = await Promise.all([
      api('/api/batches'),
      api('/api/pots'),
      api('/api/cert-events'),
    ])
  }

  async function save() {
    error = ''
    try {
      await api('/api/batches', {
        method: 'POST',
        body: JSON.stringify({
          herb,
          pot_id: Number(potId),
          steps: [{ name: '清炒', temp_c: Number(tempC), minutes: Number(minutes) }],
        }),
      })
      await loadAll()
    } catch (err) {
      error = err.message
    }
  }

  async function registerPot() {
    certMsg = ''
    try {
      await api('/api/pots', {
        method: 'POST',
        body: JSON.stringify({ name: newPotName, cert_no: newCertNo }),
      })
      newCertNo = ''
      await loadAll()
    } catch (err) {
      certMsg = err.message
    }
  }

  function startRename(pot) {
    editingId = pot.id
    editingName = pot.name
    certMsg = ''
  }

  async function saveRename() {
    certMsg = ''
    try {
      await api(`/api/pots/${editingId}`, {
        method: 'PATCH',
        body: JSON.stringify({ name: editingName }),
      })
      editingId = null
      await loadAll()
    } catch (err) {
      certMsg = err.message
    }
  }

  async function voidPot(pot) {
    certMsg = ''
    try {
      await api(`/api/pots/${pot.id}/void`, { method: 'POST' })
      await loadAll()
    } catch (err) {
      certMsg = err.message
    }
  }

  function leave() {
    localStorage.clear()
    token = ''
    role = ''
    me = ''
  }

  function fmt(t) {
    return new Date(t).toLocaleString()
  }

  $: validPots = pots.filter((p) => p.cert_status === '有效')

  if (token) loadAll()
</script>

{#if !token}
  <main>
    <h1>饮片炮制记录台</h1>
    <p>炮制记录整包保存。清炒温度须在 80 到 150，时长须在 5 到 30 分钟。每口锅挂有效校准证书才能开炒。</p>
    <input bind:value={username} />
    <input type="password" bind:value={password} />
    <button on:click={enter}>登录</button>
    {#if error}<p class="err">{error}</p>{/if}
    <p>processor / herb123456 可写；checker / check123456 只读</p>
  </main>
{:else}
  <nav class="topbar">
    <span class="brand">饮片炮制记录台</span>
    <button class:active={view === 'batches'} on:click={() => (view = 'batches')}>记录台</button>
    <button class:active={view === 'certs'} on:click={() => (view = 'certs')}>证书台</button>
    <span class="who">{me} · {role === 'writer' ? '炮制员' : '质检员'}</span>
    <button on:click={leave}>退出</button>
  </nav>

  <main>
    {#if view === 'batches'}
      <h2>炮制记录</h2>
      {#if role === 'writer'}
        <section class="card">
          <input bind:value={herb} placeholder="饮片" />
          <select bind:value={potId}>
            <option value="" disabled>选择锅（须挂有效证书）</option>
            {#each pots as pot}
              <option value={pot.id}>
                {pot.name} · {pot.cert_no}{pot.cert_status === '有效' ? '（证书有效）' : '（证书已作废）'}
              </option>
            {/each}
          </select>
          <input type="number" bind:value={tempC} />
          <input type="number" bind:value={minutes} />
          <button on:click={save} disabled={!potId}>写入清炒记录</button>
          {#if pots.length === 0}
            <p class="hint">尚无挂证锅，请先到顶栏「证书台」登记锅与证书。</p>
          {:else if validPots.length === 0}
            <p class="hint">所有锅的证书均已作废，请到「证书台」重新登记后再开炒。</p>
          {/if}
          {#if error}<p class="err">{error}</p>{/if}
        </section>
      {/if}
      <table>
        <thead>
          <tr><th>饮片</th><th>锅</th><th>证书号</th><th>结论</th><th>原因</th><th>清炒</th><th>记录人</th></tr>
        </thead>
        <tbody>
          {#each rows as row}
            <tr>
              <td>{row.herb}</td>
              <td>{row.pot_name || '旧档'}</td>
              <td>{row.cert_no || '—'}</td>
              <td>{row.verdict}</td>
              <td>{row.reason}</td>
              <td>{row.doc.steps[0].temp_c}℃ · {row.doc.steps[0].minutes} 分钟</td>
              <td>{row.created_by}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    {:else}
      <h2>证书台</h2>
      {#if role === 'writer'}
        <section class="card">
          <input bind:value={newPotName} placeholder="锅名，如 甲锅" />
          <input bind:value={newCertNo} placeholder="校准证书编号" />
          <button on:click={registerPot}>登记证书</button>
        </section>
      {/if}
      {#if certMsg}<p class="err">{certMsg}</p>{/if}

      <h3>有效证书</h3>
      <table>
        <thead>
          <tr>
            <th>锅名</th><th>证书编号</th><th>状态</th><th>登记人</th><th>更新时间</th>
            {#if role === 'writer'}<th>操作</th>{/if}
          </tr>
        </thead>
        <tbody>
          {#each validPots as pot}
            <tr>
              {#if editingId === pot.id}
                <td><input bind:value={editingName} /></td>
              {:else}
                <td>{pot.name}</td>
              {/if}
              <td>{pot.cert_no}</td>
              <td>{pot.cert_status}</td>
              <td>{pot.created_by}</td>
              <td>{fmt(pot.updated_at)}</td>
              {#if role === 'writer'}
                <td>
                  {#if editingId === pot.id}
                    <button on:click={saveRename}>保存</button>
                    <button on:click={() => (editingId = null)}>取消</button>
                  {:else}
                    <button on:click={() => startRename(pot)}>改名</button>
                    <button class="danger" on:click={() => voidPot(pot)}>作废</button>
                  {/if}
                </td>
              {/if}
            </tr>
          {:else}
            <tr><td colspan="6" class="empty">暂无有效证书</td></tr>
          {/each}
        </tbody>
      </table>

      <h3>证书大事记</h3>
      <table>
        <thead>
          <tr><th>时间</th><th>锅名</th><th>证书号</th><th>动作</th><th>说明</th><th>操作人</th></tr>
        </thead>
        <tbody>
          {#each events as ev}
            <tr>
              <td>{fmt(ev.created_at)}</td>
              <td>{ev.pot_name}</td>
              <td>{ev.cert_no}</td>
              <td>{ev.action}</td>
              <td>{ev.detail}</td>
              <td>{ev.actor}</td>
            </tr>
          {:else}
            <tr><td colspan="6" class="empty">暂无事件</td></tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </main>
{/if}

<style>
  main { font-family: sans-serif; max-width: 880px; margin: 24px auto; color: #3f2f1f; }
  h1, h2 { color: #7c2d12; }
  h3 { color: #7c2d12; border-bottom: 2px solid #e7d8c9; padding-bottom: 4px; margin-top: 28px; }
  input, select { margin-right: 8px; padding: 6px; }
  button { padding: 6px 12px; cursor: pointer; }
  .topbar {
    display: flex; align-items: center; gap: 10px;
    background: #7c2d12; color: #fff7ed; padding: 10px 20px;
    font-family: sans-serif;
  }
  .topbar .brand { font-weight: bold; margin-right: 16px; }
  .topbar button {
    background: transparent; color: #fff7ed; border: 1px solid #b45309; border-radius: 4px;
  }
  .topbar button.active { background: #fff7ed; color: #7c2d12; }
  .topbar .who { margin-left: auto; font-size: 14px; }
  .card { background: #faf3ec; border: 1px solid #e7d8c9; border-radius: 6px; padding: 12px; margin-bottom: 16px; }
  table { border-collapse: collapse; width: 100%; margin-top: 8px; }
  th, td { border: 1px solid #e7d8c9; padding: 6px 10px; text-align: left; font-size: 14px; }
  th { background: #f3e8dc; }
  .err { color: #b91c1c; }
  .hint { color: #92600a; }
  .empty { color: #8a7a6a; text-align: center; }
  .danger { color: #b91c1c; }
</style>
