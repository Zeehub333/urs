// fields/RadioField1 — Material RadioField distinct v1
// unique: basic — hash 0ca8
export class RadioField1 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var v=p.value||''; var cfg=p.cfg||{}; return '<div class="md-text-field field radiofield1"><input type="'+(cfg.type||'text')+'" value="'+v+'" placeholder="'+(cfg.placeholder||'')+'" /><label>'+(cfg.label||'RadioField1')+' — basic</label><div style="font-size:11px;color:#666">'+(cfg.helper||'basic')+'</div></div>'; }
}