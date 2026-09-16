// fields/CheckboxField1 — Material CheckboxField distinct v1
// unique: basic — hash 3ae5
export class CheckboxField1 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var v=p.value||''; var cfg=p.cfg||{}; return '<div class="md-text-field field checkboxfield1"><input type="'+(cfg.type||'text')+'" value="'+v+'" placeholder="'+(cfg.placeholder||'')+'" /><label>'+(cfg.label||'CheckboxField1')+' — basic</label><div style="font-size:11px;color:#666">'+(cfg.helper||'basic')+'</div></div>'; }
}