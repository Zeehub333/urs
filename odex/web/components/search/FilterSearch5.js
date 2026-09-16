// search/FilterSearch5 — Material FilterSearch distinct v5
// unique: with history — hash eec6
export class FilterSearch5 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var q=p.query||''; return '<div class="md-card search filtersearch5 filtersearch"><div style="font-size:11px;color:var(--md-primary);margin-bottom:4px">FilterSearch — with history (eec6aa)</div><div style="display:flex;gap:8px"><input value="'+q+'" placeholder="'+'with history'+'" style="flex:1;padding:8px;border:1px solid #ccc;border-radius:999px"/><button class="md-btn md-btn-primary">بحث</button></div><div style="font-size:11px;color:#666;margin-top:4px">with history — '+q+'</div></div>'; }
}