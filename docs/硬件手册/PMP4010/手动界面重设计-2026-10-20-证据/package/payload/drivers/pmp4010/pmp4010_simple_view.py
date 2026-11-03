"""Pure presentation for the manual tool; no transport, reads or control writes.

Grid contains plain text only. Optional row attributes are applied at the final
terminal boundary, never mixed into column measurement or the shared renderer.
"""
from fractions import Fraction
import os
from types import SimpleNamespace

from tui import Grid, Rect, draw_box, fit, _display_width, render
from pmp4010_faults import format_fault
import pmp4010_position as position

RESET = '\x1b[0m'
PALETTE = {'title':'1;36', 'section':'36', 'metric':'1', 'ok':'32',
           'warning':'1;33', 'danger':'1;31', 'muted':'2', 'action':'1;36'}


def wrap(text, width):
    """Wrap whole glyphs, including long protocol identifiers, without loss."""
    width=max(1,width)
    out=[]
    for logical in str(text).split('\n'):
        line=''
        for glyph in logical:
            if line and _display_width(line+glyph)>width:
                out.append(line);line=''
            line+=glyph
        out.append(line)
    return out


def frame(prev, lines, styles, color=True):
    if not color:
        return render(prev,lines)
    # Attributes are deliberately row based. Repaint changed rows completely so
    # shrinking text and state-color changes cannot leave stale terminal style.
    out=['\x1b[0m\x1b[2J\x1b[H'] if prev is None else []
    for y,line in enumerate(lines):
        if prev is None or y>=len(prev) or line!=prev[y]:
            code=PALETTE.get(styles.get(y),'0')
            out.append('\x1b[{};1H\x1b[{}m{}{}'.format(y+1,code,line,RESET))
    return out


def color_enabled():
    return 'NO_COLOR' not in os.environ and os.environ.get('TERM','')!='dumb'


def telemetry(app):
    c=app.controller;row=c.row(c.node);v=(row or {}).get('values',{})
    historical=bool(row and (row.get('lost') or row.get('quarantined') or
                              not row.get('online') or not row.get('updated') or
                              app._sample_age(row)>1.5))
    state='未连接';tone='warning'
    if row:
        state='在线 / 未知';tone='warning'
        if historical:
            session=c.manual_sessions.get(c.node)
            isolated=bool(session and getattr(session.client,'quarantined',False))
            state='通信隔离 / 当前实读状态未知' if isolated else '保护锁定 / 当前实读状态未知' if row.get('quarantined') else '采样丢失 / 当前实读状态未知'
            tone='danger'
        elif v:
            p=c.tool._canopen
            state={p.CIA402_DISABLED:'在线 / 未使能',p.CIA402_READY:'在线 / 就绪，未使能',
                p.CIA402_SWITCHED_ON:'在线 / 上电，未使能',p.CIA402_QUICK_STOP:'在线 / 快停状态',
                p.CIA402_ENABLED:('在线 / 使能保持' if v.get('controlword',0)&0x100 else
                                 '在线 / 运行' if v.get('actual_velocity') else '在线 / 使能，零速')}.get(c._state(v),'在线 / 状态未知')
            tone='ok' if c._state(v)==p.CIA402_ENABLED else 'warning'
            if v.get('statusword',0)&p.CIA402_FAULT or v.get('error_code_object'):
                state='在线 / 故障';tone='danger'
    coordinate='-- '+c.unit
    try:
        scale=c.coordinate_scale((row or {}).get('calibration'))
        if c.software_zero is not None and 'actual_position' in v:
            coordinate=app._number((v['actual_position']-c.software_zero)*scale)+' '+c.unit
    except (ValueError,TypeError,KeyError):pass
    speed=c.feedback_speed(row)
    if speed.startswith('未可换算'):speed='-- '+('mm/s' if c.linear else 'rpm')
    return row,v,historical,state,tone,coordinate,speed


def stop_status(row):
    cleanup=(row or {}).get('cleanup_diagnostic')
    if not cleanup:return None
    if cleanup.get('disabled_confirmed') is True:return '停止：已确认失能；机械停止未知'
    if cleanup.get('stop_attempted'):return '停止：已执行但失能未确认；请使用独立急停'
    return '停止：未执行；机械停止未知，请使用独立急停'


def diagnostics(app,row,v,historical):
    c=app.controller
    def word(value):return '未知' if value is None else '0x{:04X}'.format(value)
    result=['采样：'+('最后有效采样（历史）；当前实读状态未知' if historical else '当前完整采样'),
        '603F='+format_fault(v.get('error_code_object'))+'  6041='+word(v.get('statusword')),
        'CW='+word(v.get('controlword'))+'  原始位置 6064={} U'.format(v.get('actual_position','未知')),
        '原始速度 606C={}；2006:12={}'.format(v.get('actual_velocity','未知'),v.get('velocity_feedback_unit','未知')),
        '软件零点={} U；会话原点={} U'.format(c.software_zero,c.origin),
        '驱动607Dh=[{}, {}] U'.format(v.get('software_position_limit','未知'),v.get('software_position_limit_max','未知')),
        '本会话允许原始位置=[{}, {}] U'.format(c.settings.get('soft_min','未知'),c.settings.get('soft_max','未知'))]
    if c.linear:result.append('换算：{} mm/电机轴转（c设置）'.format(c.mm_per_motor_revolution if c.mm_per_motor_revolution is not None else '未设置'))
    if row and row.get('calibration'):
        result+=['标定 '+key+'='+str(value) for key,value in sorted(row['calibration'].items())]
        try:result.append('下次速度 '+c.speed_line(row))
        except (ValueError,TypeError,KeyError):pass
    failure=(row or {}).get('failure_diagnostic')
    if failure:
        result+=['历史失败诊断（'+('非完整快照' if failure.get('snapshot_complete') is False else '阶段采样')+'）',
            '603F='+format_fault(failure.get('fault_code'))+'  6041='+word(failure.get('raw_statusword')),
            'CW='+word(failure.get('controlword'))+'（'+failure.get('controlword_source','来源未知')+'） '+str(failure.get('decoded_state') or '状态未知')]
    if stop_status(row):result.append(stop_status(row))
    if c.blocked:result.append('保护锁定：'+str((row or {}).get('quarantine_reason') or c.blocked))
    result+=['软件行程未设限；驱动607Dh与Int32保护。' if c.linear else '固定会话原点，单动作与半范围均受1度硬顶。']
    result+=['OD 未核对项（会话授权不改变可信度）']
    result += [e['key']+': '+e['status'] for e in (c.od or {}).get('entries',[]) if e['status']!='verified']
    result+=['最近提示：'+app.progress,'操作与保护记录（旧记录保持）']
    result += [tag+' '+line for tag,line in app.log._entries]
    result+=['协议日志']+[tag+' '+line for tag,line in app.protocol_log._entries]
    return result


def help_lines(c):
    return ['移动与坐标',
        'r/j：相对位移；p：软件绝对位置（mm）。' if c.linear else 'j：相对位移；p：软件绝对位置（电机轴度）；r：固定原点半范围<=1度。',
        '输入位移后 Enter 审阅冻结目标，再 Enter 执行这一次；Esc 取消。',
        'f/b：正向/反向步进；e：保持当前位置并使能（Halt保持）。均需审阅确认。',
        'z：新鲜采样设本会话软件零点；不运动、不写607Ch、不回零/保存。',
        'z只改坐标；原物理目标与保护继续监控，不能绕过限位。',
        '速度与设置', 's：'+('直线mm/s' if c.linear else '电机轴rpm')+'；a：点动步长（默认0.1'+c.unit+'）。',
        'c：mm/电机轴转，先d失能。换算须来自现场实测。' if c.linear else 'r：设置固定原点两侧的测试半范围，须先失能，不超过1度。',
        's/a/c/z改变使旧审阅失效；量化向零，零位移拒绝；设置不立即改变活动目标。',
        '停止与保护', 'Space/x：任何页面立即请求快停；d：审阅失能；q：安全退出。',
        't：审阅Warning 2312手动复位；Enter仅确认复位，不使能、不重放目标。',
        '其他保护/真实SDO隔离不复位；复位等待按已验证主机策略执行。',
        '故障/跟随误差/越界立即锁定；通信隔离后不重试。',
        '软件停止不能替代独立急停；机械停止/抱闸仍须现场确认。',
        '诊断', 'i：OD、603F、6041、CW、原始位置、设备限位、标定、失败与协议日志。',
        '动作提交→012F保持Halt→重写冻结607A→002F/003F/002F，核对新ACK 0→1→0。',
        '上下键逐行，PgUp/PgDn翻页，Home/End首尾；Esc/?返回。']


def confirmation(app):
    r=app.pending;c=app.controller
    if app.action in ('reset','disable'):
        return ([r['lines'][0]]+['  '+', '.join(line.strip() for line in r['lines'][1:-3][i:i+2]) for i in range(0,len(r['lines'][1:-3]),2)]+r['lines'][-3:] if app.action=='reset' else
                ['失能：撤销当前使能/目标证明；机械停止仍未知。','Enter 确认失能；Esc 取消'])
    # Every value is frozen in the sealed review. Never use live feedback,
    # current settings, or c.speed_line() to reconstruct this target/speed.
    frozen=SimpleNamespace(linear=r['linear'],mm_per_motor_revolution=r['conversion'])
    scale=type(c).coordinate_scale(frozen,r['calibration'])
    coordinate_unit='mm' if r['linear'] else '电机轴度'
    n=app._number;zero=r['software_zero'];start=r['start'];target=r['target']
    title={'forward':'正向步进','backward':'反向步进','hold':'使能保持','relative':'相对移动','absolute':'绝对移动'}[r['kind']]
    request=r.get('requested_mm_s') if r['linear'] else r.get('requested_rpm')
    actual=position.unit_to_velocity(r['velocity'],r['calibration'])
    if r['linear']:actual=actual*r['conversion']/60
    unit='mm/s' if r['linear'] else 'rpm'
    speed='请求 '+n(request)+' '+unit if request is not None else '默认设置（原始速度见诊断）'
    return [
        '冻结起点  '+n((start-zero)*scale)+' '+coordinate_unit,
        '冻结目标  '+n((target-zero)*scale)+' '+coordinate_unit,
        '实际位移  '+n((target-start)*scale)+' '+coordinate_unit,
        '输入请求  '+n(r['requested'])+' '+coordinate_unit if r.get('requested') is not None else
        '保持冻结位置（不请求位移）' if r['kind']=='hold' else
        '请求步长  '+n(r['step_mm'] if r['linear'] else r['step_degrees'])+' '+coordinate_unit,
        '速度 '+speed, '量化后速度 '+n(actual)+' '+unit,
        ]


def build(app):
    grid=Grid(app.rows,app.cols,strict=(app.box!='ascii'));app._row_styles={}
    rows,cols=app.rows,app.cols;left=2 if cols>=40 else 1;width=max(1,cols-left*2)
    def put(y,text,style=None):
        if 0<=y<rows:
            grid.put(y,left,fit(text,width))
            if style:app._row_styles[y]=style
    def section(y,text):
        put(y,('--- ' if app.box=='ascii' else '── ') +text+' '+('-' if app.box=='ascii' else '─')*max(0,width-_display_width(text)-4),'section')
    row,v,historical,state,tone,coordinate,speed=telemetry(app);c=app.controller
    protected=bool(historical or c.blocked or v.get('error_code_object'))
    title='PMP4010  手动测程' if c.linear else 'PMP4010  手动测试'
    put(0,title,'title')
    interface=c.context[0] if c.context else getattr(c.args,'interface','未连接')
    put(1,'节点 {}  /  {}  /  {}'.format(c.node,interface,'直线坐标' if c.linear else '电机轴角度'),'muted')
    put(3,state+('  '+format_fault((row or {}).get('failure_diagnostic',{}).get('fault_code',v.get('error_code_object'))) if (row or {}).get('failure_diagnostic') or v.get('error_code_object') else ''),tone)
    if historical:put(4,'最后有效采样（历史），不可作为当前反馈','warning')
    compact=rows<22
    if cols>=72:
        half=width//2
        put(5,'软件位置'+('（历史）' if historical else '')+' '*(max(1,half-8-(8 if historical else 0)))+'实际速度'+('（历史）' if historical else ''),'muted')
        put(6,fit(coordinate,half-1).ljust(half-_display_width(fit(coordinate,half-1))+len(fit(coordinate,half-1)))+speed,'metric')
    else:
        put(5,('历史软件坐标 ' if historical else '软件位置 ')+coordinate,'metric')
        put(6,('历史速度 ' if historical else '实际速度 ')+speed,'metric')
    if rows<20 or cols<64:
        put(max(0,rows-4),'屏幕过小；请扩大至64x20后操作','warning')
        put(max(0,rows-3),'请扩大终端，Enter不可执行','action')
        put(max(0,rows-2),'Space/x 快停  q 退出' if cols>=38 else 'x 停止 q 退出' if cols>=16 else 'x q','danger')
        return grid
    # A previous stop uncertainty must not cover a newer real input/action error.
    if app.log._entries and app.log._entries[-1]==('[x]',app.progress):
        put(7,app.progress,'danger')
    section(8, ({'forward':'正向步进','backward':'反向步进','hold':'使能保持','relative':'相对移动','absolute':'绝对移动','reset':'手动复位','disable':'失能'}.get(app.action,'')+' / 动作确认') if app.current_view=='confirm' else {'edit':'输入','confirm':'动作确认','help':'帮助','diagnostics':'诊断','rt':'连接授权','authorize':'会话授权'}.get(app.current_view,'运动操作（保护锁定）' if protected else '运动操作'))
    end=rows-4
    content=[]
    if app.current_view in ('help','diagnostics'):
        content=help_lines(c) if app.current_view=='help' else diagnostics(app,row,v,historical)
    elif app.current_view=='confirm' and app.pending:content=confirmation(app)
    elif app.current_view=='edit':
        labels={'speed':'速度 '+('mm/s' if c.linear else 'rpm'),'step':'步长 '+c.unit,'range':'原点两侧半范围 电机轴度（<=1）',
                'relative':'相对位移 '+c.unit,'absolute':'软件绝对位置 '+c.unit,'conversion':'每电机轴转对应 mm（先d失能）'}
        content=[labels[app.edit_kind],'> '+(app.draft or '请输入数值')+' _',
            '可用正负数或分数，例如 +1.2、-1/10。' if app.edit_kind in ('relative','absolute') else '可用正数或分数。',
            'Enter 审阅目标；Esc 取消' if app.edit_kind in ('relative','absolute') else 'Enter 提交设置；Esc 取消']
        if app.edit_kind in ('relative','absolute') and app.draft:
            try:
                requested=app._signed(app.draft);scale=c.coordinate_scale(row['calibration'])
                base=v['actual_position'] if app.edit_kind=='relative' else c.software_zero
                target=base+int(requested/scale)
                content+=['草稿目标 '+app._number((target-c.software_zero)*scale)+' '+c.unit+'（尚未冻结）']
                if not c.settings['soft_min']<=base+requested/scale<=c.settings['soft_max']:content+=['草稿超出允许行程，禁止执行。']
                elif target==v['actual_position']:content+=['量化后位移为零，禁止执行。']
            except (ValueError,KeyError,TypeError) as exc:content+=[str(exc)]
    elif app.current_view=='rt':content=['请先协调 RT 控制器总线占用与现场安全。','连接仅扫描和读取，不会自动使能或运动。','Enter 同意本会话连接；q 退出。']
    elif app.current_view=='authorize':
        content=['接受以下 OD 未核对项及独立停机责任：']
        gaps=[e['key'] for e in (c.od or {}).get('entries',[]) if e['status']!='verified']
        content += [', '.join(gaps[i:i+2]) for i in range(0,len(gaps),2)]
        content += ['软件行程未设限；驱动607Dh、Int32保护。' if c.linear else '本会话固定原点半范围<=1度，与驱动607Dh取交。',
            'Enter 授权本会话并初始化软件零点。']
    elif app.current_view=='connecting':content=['正在只读连接，请稍候…']
    else:
        content=['[ f ] 正向步进       [ b ] 反向步进',
            '[ r/j ] 相对移动     [ p ] 绝对移动' if c.linear else '[ j ] 相对移动       [ p ] 绝对移动',
            '[ z ] 设软件零点     每次运动均须 Enter 确认', '' if compact else '@settings']
        try:
            _,actual,_=c.quantities(row)
            speedline=c.speed_line(row).split('（')[0]
            if (c.requested_mm_s if c.linear else c.requested_rpm) is None:
                speedline='设置 '+c.display_speed(position.unit_to_velocity(c.next_velocity,row['calibration']),row['calibration'])
            content+=['s 速度  '+speedline,'a 步长  '+app._number(c.step)+' '+c.unit+'  /  量化 '+app._number(actual)+' '+c.unit]
        except (ValueError,TypeError,KeyError):content+=['c 设置 mm/电机轴转，完成换算后才能运动' if c.linear else '等待完整标定采样']
        if compact:content=[line for line in content if line]
        content+=['e 使能保持    d 失能    t 手动复位',
            'c 换算设置（mm/电机轴转）' if c.linear else 'r 测试范围（原点两侧）']
    wrapped=[part for line in content for part in wrap(line,width)]
    capacity=max(1,end-9)
    app._page_size=capacity
    maximum=max(0,len(wrapped)-capacity)
    app.page_offset=max(0,min(app.page_offset,maximum))
    # Main controls/settings are intrinsically short; paged views retain every
    # line at small sizes instead of silently dropping confirmation or OD text.
    start=app.page_offset if app.current_view in ('help','diagnostics','confirm','authorize') else 0
    for y,line in enumerate(wrapped[start:start+capacity],9):
        if line=='@settings':section(y,'下次动作设置')
        else:put(y,line,'muted' if protected and line.startswith('[ ') else 'action' if line.startswith(('> ','Enter','冻结目标','[ ')) else None)
    if len(wrapped)>capacity:
        put(end,'{}/{}  上下/PgUp/PgDn 翻阅  Home/End 首尾'.format(start+1,len(wrapped)),'muted')
    else:put(end,'','muted')
    # Critical faults and stop uncertainty stay on every view, including modal
    # inputs. Historic raw samples live in diagnostics, with this direct signal.
    failure=(row or {}).get('failure_diagnostic');stop=stop_status(row)
    fault=format_fault(failure.get('fault_code') if failure else v.get('error_code_object'))
    quiet=app.progress.startswith(('等待连接授权','会话授权完成','会话已授权','请核对本次动作','本次指令已提交','软件坐标设置完成'))
    alert=stop if stop else '故障 '+fault if failure or v.get('error_code_object') else '' if quiet else app.progress
    put(rows-3,alert,'danger' if stop or failure or v.get('error_code_object') or historical else 'muted')
    if app.current_view=='confirm':put(rows-2,'Enter 仅确认本次复位 / Esc 取消' if app.action=='reset' else 'Enter 确认 / Esc 取消','action')
    elif app.current_view=='edit':put(rows-2,'Enter 审阅目标 / Esc 取消' if app.edit_kind in ('relative','absolute') else 'Enter 提交设置 / Esc 取消','action')
    else:put(rows-2,'i 诊断    ? 帮助' if app.current_view=='main' else 'Esc / i 返回' if app.current_view=='diagnostics' else 'Esc / ? 返回' if app.current_view=='help' else '','muted')
    put(rows-1,'Space/x 快停    q 安全退出','danger')
    return grid
