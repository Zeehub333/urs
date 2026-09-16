// fields/CheckboxField6 — Material CheckboxField distinct v6
// unique: floating label — hash 923b
export class CheckboxField6 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var v=p.value||''; var cfg=p.cfg||{}; return '<div class="md-text-field field checkboxfield6"><input type="'+(cfg.type||'text')+'" value="'+v+'" placeholder="'+(cfg.placeholder||'')+'" /><label>'+(cfg.label||'CheckboxField6')+' — floating label</label><div style="font-size:11px;color:#666">'+(cfg.helper||'floating label')+'</div></div>'; }
}