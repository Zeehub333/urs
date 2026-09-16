// fields/DateField1 — Material DateField distinct v1
// unique: basic — hash e712
export class DateField1 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var v=p.value||''; var cfg=p.cfg||{}; return '<div class="md-text-field field datefield1"><input type="'+(cfg.type||'text')+'" value="'+v+'" placeholder="'+(cfg.placeholder||'')+'" /><label>'+(cfg.label||'DateField1')+' — basic</label><div style="font-size:11px;color:#666">'+(cfg.helper||'basic')+'</div></div>'; }
}