// fields/TextField6 — Material TextField distinct v6
// unique: floating label — hash cddf
export class TextField6 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var v=p.value||''; var cfg=p.cfg||{}; return '<div class="md-text-field field textfield6"><input type="'+(cfg.type||'text')+'" value="'+v+'" placeholder="'+(cfg.placeholder||'')+'" /><label>'+(cfg.label||'TextField6')+' — floating label</label><div style="font-size:11px;color:#666">'+(cfg.helper||'floating label')+'</div></div>'; }
}