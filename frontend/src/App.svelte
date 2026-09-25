<script>
  let username = 'processor'
  let password = 'herb123456'
  let token = localStorage.getItem('herb_token') || ''
  let role = localStorage.getItem('herb_role') || ''
  let view = 'batches'
  let rows = []
  let pots = []
  let events = []
  let herb = '白芍'
  let batchPot = ''
  let tempC = 110
  let minutes = 10
  let error = ''
  let newPotName = ''
  let newCertNo = ''
  let certError = ''
  let renamingId = 0
  let renameValue = ''

  $: validPots = pots.filter((p) => p.status === '有效')

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
    const data = await api('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    })
    token = data.access_token
    role = data.role
    localStorage.setItem('herb_token', token)
    localStorage.setItem('herb_role', role)
    await load()
  }

  async function load() {
    ;[rows, pots, events] = await Promise.all([
      api('/api/batches'),
      api('/api/pots'),
      api('/api/cert-events'),
    ])
    if (!validPots.some((p) => p.name === batchPot)) batchPot = validPots[0]?.name || ''
  }

  async function switchView(next) {
    view = next
    certError = ''
    error = ''
    await load()
  }

  async function save() {
    error = ''
    try {
      await api('/api/batches', {
        method: 'POST',
        body: JSON.stringify({
          herb,
          pot_name: batchPot,
          steps: [{ name: '清炒', temp_c: Number(tempC), minutes: Number(minutes) }],
        }),
      })
      await load()
    } catch (err) {
      error = err.message
    }
  }

  async function registerPot() {
    certError = ''
    try {
      await api('/api/pots', {
        method: 'POST',
        body: JSON.stringify({ name: newPotName, cert_no: newCertNo }),
      })
      newPotName = ''
      newCertNo = ''
      await load()
    } catch (err) {
      certError = err.message
    }
  }

  async function voidPot(pot) {
    certError = ''
    try {
      await api(`/api/pots/${pot.id}/void`, { method: 'POST' })
      await load()
    } catch (err) {
      certError = err.message
    }
  }

  function startRename(pot) {
    renamingId = pot.id
    renameValue = pot.name
  }

  async function confirmRename(pot) {
    certError = ''
    try {
      await api(`/api/pots/${pot.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ name: renameValue }),
      })
      renamingId = 0
      await load()
    } catch (err) {
      certError = err.message
    }
  }

  function fmt(ts) {
    return new Date(ts).toLocaleString()
  }

  function leave() {
    localStorage.clear()
    token = ''
    role = ''
  }

  if (token) load()
</script>

<main>
  <h1>饮片炮制记录台</h1>
  {#if !token}
    <p>炮制记录整包保存。清炒温度须在 80 到 150，时长须在 5 到 30 分钟。每口锅须挂有效校准证书才能开炒。</p>
    <input bind:value={username} />
    <input type="password" bind:value={password} />
    <button on:click={enter}>登录</button>
    <p>processor / herb123456 可写；checker / check123456 只读</p>
  {:else}
    <nav>
      <a href="#/" class:active={view === 'batches'} on:click|preventDefault={() => switchView('batches')}>记录台</a>
      <a href="#/certs" class:active={view === 'certs'} on:click|preventDefault={() => switchView('certs')}>证书台</a>
      <button on:click={leave}>退出</button>
    </nav>

    {#if view === 'batches'}
      {#if role === 'writer'}
        <input bind:value={herb} placeholder="饮片" />
        <select bind:value={batchPot}>
          {#each validPots as pot}
            <option value={pot.name}>{pot.name}（证书 {pot.cert_no}）</option>
          {:else}
            <option value="" disabled>暂无有效证书的锅，请先到证书台登记</option>
          {/each}
        </select>
        <input type="number" bind:value={tempC} />
        <input type="number" bind:value={minutes} />
        <button on:click={save} disabled={!batchPot}>写入清炒记录</button>
        {#if error}<p class="err">{error}</p>{/if}
      {/if}
      <ul>
        {#each rows as row}
          <li>{row.herb} · {row.pot_name || '未挂锅'} · {row.verdict} · {row.reason} · 温度 {row.doc.steps[0].temp_c}</li>
        {/each}
      </ul>
    {:else}
      <section>
        <h2>有效证书</h2>
        {#if role === 'writer'}
          <div class="desk-form">
            <input bind:value={newPotName} placeholder="锅名" />
            <input bind:value={newCertNo} placeholder="校准证书号" />
            <button on:click={registerPot}>登记证书</button>
          </div>
        {/if}
        {#if certError}<p class="err">{certError}</p>{/if}
        <table>
          <thead>
            <tr><th>锅名</th><th>证书号</th><th>登记人</th>{#if role === 'writer'}<th>操作</th>{/if}</tr>
          </thead>
          <tbody>
            {#each validPots as pot}
              <tr>
                <td>
                  {#if renamingId === pot.id}
                    <input bind:value={renameValue} />
                    <button on:click={() => confirmRename(pot)}>确定</button>
                    <button on:click={() => (renamingId = 0)}>取消</button>
                  {:else}
                    {pot.name}
                  {/if}
                </td>
                <td>{pot.cert_no}</td>
                <td>{pot.created_by}</td>
                {#if role === 'writer'}
                  <td>
                    {#if renamingId !== pot.id}
                      <button on:click={() => startRename(pot)}>改名</button>
                      <button on:click={() => voidPot(pot)}>作废</button>
                    {/if}
                  </td>
                {/if}
              </tr>
            {:else}
              <tr><td colspan="4">暂无有效证书</td></tr>
            {/each}
          </tbody>
        </table>
      </section>
      <section>
        <h2>证书大事记</h2>
        <ul>
          {#each events as ev}
            <li>{fmt(ev.created_at)} · {ev.action} · {ev.detail} · 操作人 {ev.actor}</li>
          {:else}
            <li>暂无大事记</li>
          {/each}
        </ul>
      </section>
    {/if}
  {/if}
</main>

<style>
  main { font-family: sans-serif; max-width: 720px; margin: 24px auto; color: #3f2f1f; }
  h1 { color: #7c2d12; }
  h2 { color: #7c2d12; font-size: 18px; }
  input, select { margin-right: 8px; padding: 6px; }
  nav { display: flex; gap: 16px; align-items: center; padding: 8px 0; border-bottom: 1px solid #d6c3b0; margin-bottom: 16px; }
  nav a { color: #7c2d12; text-decoration: none; }
  nav a.active { font-weight: bold; border-bottom: 2px solid #7c2d12; }
  table { border-collapse: collapse; width: 100%; margin-top: 8px; }
  th, td { border: 1px solid #d6c3b0; padding: 6px 10px; text-align: left; }
  section { margin-bottom: 24px; }
  .desk-form { margin-bottom: 8px; }
  .err { color: #b91c1c; }
</style>
