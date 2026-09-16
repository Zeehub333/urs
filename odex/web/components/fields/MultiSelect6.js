// fields/MultiSelect6 — Material MultiSelect distinct v6
// unique: floating label — hash 0d4e
export class MultiSelect6 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var v=p.value||''; var cfg=p.cfg||{}; return '<div class="md-text-field field multiselect6"><input type="'+(cfg.type||'text')+'" value="'+v+'" placeholder="'+(cfg.placeholder||'')+'" /><label>'+(cfg.label||'MultiSelect6')+' — floating label</label><div style="font-size:11px;color:#666">'+(cfg.helper||'floating label')+'</div></div>'; }
}