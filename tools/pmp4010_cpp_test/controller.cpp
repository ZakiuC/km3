#include "controller.hpp"
#include "socketcan_checked.hpp"
#include "../../device/drivers/pmp4010/driver/pmp4010_velocity.hpp"
#include <algorithm>
#include <iomanip>
#include <optional>
#include <sstream>

namespace pmp4010_cpp_test {
namespace {
using Calibration = km3::pmp4010::VelocityCalibration;
using Clock = nimotion::Clock;
class Cancelled final : public std::runtime_error {public:Cancelled():std::runtime_error("动作已取消") {}};
class AlarmError final : public std::runtime_error {public:using std::runtime_error::runtime_error;};
std::string Number(long double value) { std::ostringstream s; s << std::setprecision(12) << value; return s.str(); }
std::uint16_t State(std::uint16_t sw) { return (sw&0x4f)==0x40 ? 0x40 : sw&0x6f; }
void Require(bool condition,const std::string& reason) { if(!condition) throw std::runtime_error(reason); }
bool Same(const Calibration& a,const Calibration& b) {
    return a.encoder_increments==b.encoder_increments && a.encoder_motor_revolutions==b.encoder_motor_revolutions
        && a.gear_motor_revolutions==b.gear_motor_revolutions && a.gear_output_revolutions==b.gear_output_revolutions;
}
km3::pmp4010::VelocityFraction Fraction(km3::pmp4010::VelocityDirection direction,const std::string& numerator,
                    const std::string& denominator,const Calibration& c) {
    km3::pmp4010::VelocityFraction value;
    Require(km3::pmp4010::ConvertVelocity(direction,numerator,denominator,c,value)==km3::pmp4010::VelocityError::Ok,
            "608F/6091标定无效或速度换算溢出");
    return value;
}
long double Convert(km3::pmp4010::VelocityDirection direction,const std::string& numerator,
                    const std::string& denominator,const Calibration& c) {
    auto value=Fraction(direction,numerator,denominator,c);
    return std::stold(value.numerator)/std::stold(value.denominator);
}
long double ToRpm(std::uint32_t units,const Calibration& c) {
    return Convert(km3::pmp4010::VelocityDirection::UnitsToRpm,std::to_string(units),"1",c);
}
// ConvertVelocity guarantees a reduced fraction whose magnitudes fit uint128.
// These helpers only compare/round that returned fraction; no velocity formula
// is duplicated here. Floating point is exclusively for display/coordinates.
__extension__ typedef unsigned __int128 Wide;
Wide Magnitude(const std::string& digits) {
    Wide value=0;for(char ch:digits){Require(ch>='0'&&ch<='9',"速度分数必须非负");value=value*10+unsigned(ch-'0');}return value;
}
std::string Decimal(Wide value) {
    std::string out;
    do{out.push_back(char('0'+unsigned(value%10)));value/=10;}while(value);
    std::reverse(out.begin(),out.end());return out;
}
bool WithinRpm(std::uint32_t units,const Calibration& c,std::uint32_t max_rpm) {
    auto fraction=Fraction(km3::pmp4010::VelocityDirection::UnitsToRpm,std::to_string(units),"1",c);
    auto n=Magnitude(fraction.numerator),d=Magnitude(fraction.denominator);
    return n/d<max_rpm||(n/d==max_rpm&&n%d==0);
}
std::uint32_t QuantizedUnits(double rpm,const Calibration& c) {
    Require(std::isfinite(rpm)&&rpm>0&&rpm<=1000000000,"rpm必须为有限正数");
    // Serialize the UI double EXACTLY as its binary rational, then call the
    // production pure C++ converter. No preliminary decimal truncation.
    // Only 6081 is quantized, explicitly to nearest integer (half away from zero).
    int exponent=0;const double mantissa=std::frexp(rpm,&exponent);
    auto significant=std::uint64_t(std::ldexp(mantissa,53));exponent-=53;
    while((significant&1)==0){significant>>=1;++exponent;}
    Wide numerator=significant,denominator=1;
    if(exponent<0){Require(exponent>-128,"rpm过小，超出精确换算范围");denominator<<=-exponent;}
    else{Require(exponent<128&&numerator<=(~Wide{0}>>exponent),"rpm超出精确换算范围");numerator<<=exponent;}
    auto fraction=Fraction(km3::pmp4010::VelocityDirection::RpmToUnits,Decimal(numerator),Decimal(denominator),c);
    auto n=Magnitude(fraction.numerator),d=Magnitude(fraction.denominator);
    auto rounded=n/d;
    const auto remainder=n%d;
    if(remainder>=d-remainder)++rounded;
    Require(rounded>=1&&rounded<=UINT32_MAX,"6081速度量化为零或溢出Uint32");
    return std::uint32_t(rounded);
}
class GuardedBus final : public nimotion::CanBus {
    nimotion::CanBus& bus_;
    unsigned node_;
    int timeout_;
    bool isolated_=false;
    Clock::time_point deadline_{};
    Clock::time_point operation_deadline_=Clock::time_point::max();
public:
    std::string reason;
    GuardedBus(nimotion::CanBus& bus,unsigned node,int timeout):bus_(bus),node_(node),timeout_(timeout) {}
    bool Isolated() const {return isolated_;}
    void SetOperationDeadline(Clock::time_point end){operation_deadline_=end;}
    void Isolate(const std::string& message) { isolated_=true; if(reason.empty()) reason=message; }
    void Check() const { Require(!isolated_,"本会话SDO已隔离；禁止重试、停止帧或重建client："+reason); }
    void send(const nimotion::Frame& f) override {
        Check();
        try {
            Require(f.id==0x600+node_&&f.size==8,"拒绝非本节点SDO帧/NMT");
            auto index=nimotion::decode(f.data.data()+1,2);
            const bool read=f.data[0]==0x40;
            const bool write=((index==0x607a||index==0x6081)&&f.data[0]==0x23)
                ||(index==0x6040&&f.data[0]==0x2b);
            Require(read||(write&&f.data[3]==0),"拒绝未授权对象下载（仅6040/607A/6081）");
            deadline_=std::min(operation_deadline_,Clock::now()+std::chrono::milliseconds(timeout_));
            bus_.send(f);
        } catch(const std::exception& e) {Isolate(e.what());throw;}
    }
    bool receive(nimotion::Frame& f,int timeout_ms) override {
        Check();
        try {
            if(timeout_ms>0) {
                auto remaining=std::chrono::duration_cast<std::chrono::milliseconds>(deadline_-Clock::now()).count();
                Require(remaining>0,"SDO总期限已到；隔离本会话");
                timeout_ms=std::min(timeout_ms,int(remaining));
            }
            bool received=bus_.receive(f,timeout_ms);
            if(timeout_ms>0) Require(Clock::now()<deadline_,"SDO总期限已到；隔离本会话");
            if(received&&f.id==0x80+node_) {
                Require(f.size==8,"EMCY帧宽度错误");
                Require(nimotion::decode(f.data.data(),2)==0,
                        "EMCY非零报警："+nimotion::hex(nimotion::decode(f.data.data(),2))+"；隔离同client");
            }
            if(received&&f.id==0x700+node_&&f.size==1&&f.data[0]==0)
                throw std::runtime_error("设备重启；隔离同client");
            return received;
        }catch(const std::exception& e){Isolate(e.what());throw;}
    }
};
struct Live {
    Snapshot view;
    Calibration cal{};
    std::uint16_t first_sw=0,selector=0,mode_select=0;
    std::int8_t mode=0,display=0;
    std::int16_t halt=0,quick=0;
    std::int32_t offset=0,low=0,high=0,first_pos=0,first_velocity=0;
    std::uint32_t max_rpm=0,max_velocity=0,profile=0,end_velocity=0,accel=0,decel=0,
        quick_decel=0,max_accel=0,max_decel=0,window=0;
};
bool Fixed(const Live& a,const Live& b) {
    return Same(a.cal,b.cal)&&a.selector==b.selector&&a.mode_select==b.mode_select&&a.mode==b.mode&&a.display==b.display
        &&a.halt==b.halt&&a.quick==b.quick&&a.offset==b.offset&&a.low==b.low&&a.high==b.high
        &&a.max_rpm==b.max_rpm&&a.max_velocity==b.max_velocity&&a.profile==b.profile&&a.end_velocity==b.end_velocity
        &&a.accel==b.accel&&a.decel==b.decel&&a.quick_decel==b.quick_decel&&a.max_accel==b.max_accel
        &&a.max_decel==b.max_decel&&a.window==b.window;
}
bool Motion(Action a) {return a==Action::Absolute||a==Action::Relative||a==Action::Forward||a==Action::Backward||a==Action::Hold;}
}

std::string StateDescription(std::uint16_t sw) {
    switch(State(sw)) {
        case 0x40:return "失能 Switch on disabled";
        case 0x21:return "准备 Ready to switch on";
        case 0x23:return "已接通 Switched on";
        case 0x27:return "已使能 Operation enabled";
        case 7:return "快停 Quick stop active";
        case 8:return "故障 Fault";
        case 0xf:return "故障反应 Fault reaction active";
        default:return "未知状态 "+nimotion::hex(sw);
    }
}
std::string FaultDescription(std::uint16_t code) {
    // E06 section 9.1 table 9-1, same exact-code display table as pmp4010_faults.py.
    switch(code) {
        case 0:return "无错误码";
        case 0x2300:return "电机过流";case 0x2311:return "电机过载";case 0x2312:return "电机堵转";
        case 0x3210:return "电源过电压";case 0x3220:return "电源欠电压";case 0x3211:return "过泄压值故障";
        case 0x3285:return "输出相故障";case 0x4210:return "温度过高报警";case 0x4220:return "温度过低报警";
        case 0x5080:return "驱动器故障";case 0x5540:return "Flash 操作故障";case 0x5541:return "Flash 初始化故障";
        case 0x5542:return "Flash 校验错误警告";case 0x5543:return "Flash 用户区无参数";case 0x5544:return "掉电位置存储异常";
        case 0x5545:return "掉电保存数据未存储上";case 0x6000:return "硬件初始化故障";
        case 0x6320:return "参数设置错误";case 0x6321:return "注册故障";
        case 0x7301:return "编码器电池低压故障";case 0x7302:return "编码器电池欠压报警";case 0x7303:return "编码器通信故障";
        case 0x7304:return "编码器多圈计数错误";case 0x7305:return "Z 脉冲故障";case 0x7306:return "编码器故障";
        case 0x7307:return "编码器警告";case 0x7309:return "编码器电池失效故障";case 0x7310:return "超速";
        case 0x7501:return "通信故障";case 0x7502:return "CAN 被动错误";case 0x7503:return "CAN 接收溢出";
        case 0x8610:return "原点回归超时";case 0x8611:return "位置超差";case 0x8613:return "软件限位错误";
        case 0x8614:return "限位开关错误";case 0x8700:return "同步故障";case 0x8615:return "曲线规划计算错误";
        case 0x8616:return "目标位置溢出";case 0x8617:return "曲线规划参数过小";
        case 0xff01:return "电机参数识别故障";case 0xff02:return "不能执行的命令";
        case 0xff05:return "STO 1 关闭失败";case 0xff06:return "STO 1 使能失败";
        case 0xff07:return "STO 2 关闭失败";case 0xff08:return "STO 2 使能失败";
        case 0xff09:return "STO 输入异常故障";case 0xff0a:return "STO 使能状态";
        case 0xff0b:return "非安全状态";case 0xff0c:return "编码器校准超时";
        default:return "未知报警";
    }
}
std::unique_ptr<nimotion::CanBus> MakeSocketCan(const std::string& interface, unsigned expected_bitrate) {
    return MakeCheckedSocketCan(interface,expected_bitrate);
}

struct Controller::Impl {
    Settings settings;
    GuardedBus bus;
    // Real vendor Servo owns our ONE persistent SdoClient. High-level vendor
    // configure/enable/move/reset are intentionally not called: their implicit
    // configuration, rebase and reset semantics differ from manual PP policy.
    // Only statusword/positionRaw/sdo are used, so touched_ stays false and its
    // destructor performs no IO. GuardedBus also blocks any isolated send.
    nimotion::Servo servo;
    Snapshot last;
    Live expected;
    bool zero_set=false,locked=false,touched=false,closed=false,proof=false,ignore_cancel=false,reset_allowed=false;
    std::int32_t zero=0,target=0;
    std::uint16_t proof_word=0;
    std::uint64_t serial=0;
    const std::atomic_bool* cancel=nullptr;
    const std::atomic_bool* attached_cancel=nullptr;
    std::uint16_t stage_word=0;
    std::optional<bool> stage_ack;
    std::optional<Calibration> session_calibration;
    std::string phase="启动只读";
    bool control_attempted=false,control_acknowledged=false;
    std::uint16_t attempted_word=0;
    std::function<void(const std::string&)> diagnostics;
    Clock::time_point operation_end=Clock::time_point::max();
    struct Frozen { ReviewPlan plan; Live live; double value; };
    std::optional<Frozen> frozen;
    std::optional<Outcome> shutdown_result;
    Impl(nimotion::CanBus& raw,Settings s):settings(std::move(s)),bus(raw,settings.node,settings.sdo_timeout_ms),servo(bus,settings.node) {
        Require(settings.node>=1&&settings.node<=127&&settings.sdo_timeout_ms>0&&settings.sdo_timeout_ms<=500&&settings.state_timeout_ms>0
            &&settings.move_timeout_ms>0&&settings.poll_interval_ms>0,"节点或期限无效");
        Require(std::isfinite(settings.rpm)&&settings.rpm>0&&std::isfinite(settings.step_mm)&&settings.step_mm>0
            &&std::isfinite(settings.mm_per_motor_turn)&&settings.mm_per_motor_turn>0,"设置必须为有限正数");
    }
    void Check() {
        bus.Check();Require(!closed,"会话已关闭");
        auto flag=cancel?cancel:attached_cancel;
        if(!ignore_cancel&&flag&&flag->load(std::memory_order_relaxed))throw Cancelled();
        Require(Clock::now()<operation_end,"动作总期限已到");
    }
    void Emit(const std::string& text) {
        if(!diagnostics)return;
        diagnostics(phase+"；"+text+"；6041="+(last.statusword_known?nimotion::hex(last.statusword):"未知")
            +"；603F="+(last.error_code_known?nimotion::hex(last.error_code)+"（"+FaultDescription(last.error_code)+"）":"未知")
            +"；6040实读="+(last.controlword_known?nimotion::hex(last.controlword):"未知")
            +"；6064="+(last.position_known?std::to_string(last.position_raw):"未知")
            +"；606C="+(last.velocity_known?std::to_string(last.velocity_raw):"未知")
            +(last.valid?"；完整快照":"；不完整快照，仅列本轮已观测字段"));
    }
    template<class F> auto Io(F fn) -> decltype(fn()) {
        Check();
        bus.SetOperationDeadline(operation_end);
        try {return fn();}
        catch(const nimotion::SdoAbort&) {locked=true;reset_allowed=false;throw;} // known device rejection, distinct from transport uncertainty
        catch(const std::exception& e) {bus.Isolate(e.what());locked=true;reset_allowed=false;throw;}
    }
    template<class T> T Read(std::uint16_t i,std::uint8_t s=0) {
        return Io([&]{return servo.sdo().read<T>(i,s);});
    }
    template<class T> void Write(std::uint16_t i,T v) {
        if(i==0x6040){control_attempted=true;control_acknowledged=false;attempted_word=std::uint16_t(v);
            last.controlword_attempted=true;last.controlword_acknowledged=false;last.attempted_controlword=attempted_word;
            Emit("6040下载尝试="+nimotion::hex(attempted_word));}
        touched=true;Io([&]{servo.sdo().write<T>(i,0,v);});
        if(i==0x6040){control_acknowledged=true;last.controlword_acknowledged=true;Emit("6040下载SDO ACK="+nimotion::hex(attempted_word));}
        auto actual=Read<T>(i);
        Require(actual==v,nimotion::hex(i)+"写读回不一致");
    }
    void Control(std::uint16_t cw) {
        Require(Read<std::uint16_t>(0x2002,1)==0,"2002:01已非CiA402，禁止控制字");
        Write<std::uint16_t>(0x6040,cw);
    }
    long double MmPerUnit(const Calibration& c) const {
        Require(c.encoder_increments&&c.encoder_motor_revolutions&&c.gear_motor_revolutions&&c.gear_output_revolutions,
                "608F/6091存在零标定值");
        return static_cast<long double>(settings.mm_per_motor_turn)*c.gear_motor_revolutions*c.encoder_motor_revolutions
            /c.gear_output_revolutions/c.encoder_increments;
    }
    Snapshot Publish(Live& l) {
        auto& v=l.view;
        if(!zero_set){zero=v.position_raw;zero_set=true;}
        v.software_zero_raw=zero;v.settings=settings;
        v.position_mm=double((static_cast<long double>(v.position_raw)-zero)*MmPerUnit(l.cal));
        Require(l.selector<=1,"未知2006:12实际速度单位");
        v.motor_rpm=l.selector==0 ? v.velocity_raw : double(Convert(km3::pmp4010::VelocityDirection::UnitsToRpm,
            std::to_string(v.velocity_raw),"1",l.cal));
        v.state=StateDescription(v.statusword);
        v.alarm=nimotion::hex(v.error_code)+"（"+FaultDescription(v.error_code)+"）";
        if(v.statusword&0x80) v.alarm+=" Warning";
        if(v.statusword&8) v.alarm+=" Fault";
        v.statusword_known=v.controlword_known=v.error_code_known=v.position_known=v.velocity_known=true;
        v.phase=phase;v.controlword_attempted=control_attempted;v.controlword_acknowledged=control_acknowledged;v.attempted_controlword=attempted_word;
        v.valid=true;v.isolated=bus.Isolated();v.action_locked=locked;last=v;return v;
    }
    Live Sample() {
        Live l;auto& v=l.view;
        last=Snapshot{};last.settings=settings;last.software_zero_raw=zero;
        last.phase=phase;last.controlword_attempted=control_attempted;last.controlword_acknowledged=control_acknowledged;last.attempted_controlword=attempted_word;
        last.message="快照尚未完成（仅known字段是本轮已返回观测）";
        l.first_sw=Io([&]{return servo.statusword();});
        v.statusword=l.first_sw;last.statusword=v.statusword;last.statusword_known=true;last.state=StateDescription(v.statusword);
        v.error_code=Read<std::uint16_t>(0x603f);last.error_code=v.error_code;last.error_code_known=true;last.alarm=nimotion::hex(v.error_code)+"（"+FaultDescription(v.error_code)+"）";
        v.error_register=Read<std::uint8_t>(0x1001);last.error_register=v.error_register;
        l.first_pos=Io([&]{return servo.positionRaw();});v.position_raw=l.first_pos;last.position_raw=v.position_raw;last.position_known=true;
        l.first_velocity=Read<std::int32_t>(0x606c);v.velocity_raw=l.first_velocity;last.velocity_raw=v.velocity_raw;last.velocity_known=true;
        v.controlword=Read<std::uint16_t>(0x6040);last.controlword=v.controlword;last.controlword_known=true;
        v.target_raw=Read<std::int32_t>(0x607a);last.target_raw=v.target_raw;
        v.demand_raw=Read<std::int32_t>(0x6062);last.demand_raw=v.demand_raw;
        l.mode_select=Read<std::uint16_t>(0x2002,1);l.mode=Read<std::int8_t>(0x6060);l.display=Read<std::int8_t>(0x6061);
        l.offset=Read<std::int32_t>(0x607c);l.low=Read<std::int32_t>(0x607d,1);l.high=Read<std::int32_t>(0x607d,2);
        l.halt=Read<std::int16_t>(0x605d);l.quick=Read<std::int16_t>(0x605a);l.selector=Read<std::uint16_t>(0x2006,0x12);
        l.max_rpm=Read<std::uint32_t>(0x6080);l.max_velocity=Read<std::uint32_t>(0x607f);
        l.profile=Read<std::uint32_t>(0x6081);l.end_velocity=Read<std::uint32_t>(0x6082);
        l.accel=Read<std::uint32_t>(0x6083);l.decel=Read<std::uint32_t>(0x6084);l.quick_decel=Read<std::uint32_t>(0x6085);
        l.max_accel=Read<std::uint32_t>(0x60c5);l.max_decel=Read<std::uint32_t>(0x60c6);l.window=Read<std::uint32_t>(0x6067);
        l.cal={Read<std::uint32_t>(0x608f,1),Read<std::uint32_t>(0x608f,2),Read<std::uint32_t>(0x6091,1),Read<std::uint32_t>(0x6091,2)};
        v.statusword=Io([&]{return servo.statusword();});last.statusword=v.statusword;last.state=StateDescription(v.statusword);
        v.position_raw=Io([&]{return servo.positionRaw();});last.position_raw=v.position_raw;
        v.velocity_raw=Read<std::int32_t>(0x606c);last.velocity_raw=v.velocity_raw;
        (void)MmPerUnit(l.cal);
        if(session_calibration&&!Same(l.cal,*session_calibration)){
            locked=true;reset_allowed=false;throw std::runtime_error("608F/6091与本会话首次完整标定不同，禁止重释软件零点；须外部恢复");
        }
        if(!session_calibration)session_calibration=l.cal;
        Publish(l);return l;
    }
    void Validate(const Live& l,std::int32_t start,std::int32_t goal,bool stationary,bool allow_alarm=false) {
        auto& v=l.view;
        if(!allow_alarm&&(((l.first_sw|v.statusword)&(8|0x2000|0x80))||v.error_code||v.error_register)) {
            reset_allowed=(!locked||reset_allowed)&&WarningReset(l);locked=true;
            throw AlarmError("报警/跟随误差："+v.alarm+"；禁止自动复位");
        }
        Require(l.mode_select==0&&l.mode==1&&l.display==1,"仅允许CiA402 PP1，模式已变化");
        Require(l.offset==0,"607Ch非零，拒绝动作");Require(l.halt==1||l.halt==2,"605Dh须为1或2");
        Require(l.end_velocity==0,"6082h末速度非零");
        Require(l.low<l.high&&start>=l.low&&start<=l.high&&goal>=l.low&&goal<=l.high
            &&l.first_pos>=l.low&&l.first_pos<=l.high&&v.position_raw>=l.low&&v.position_raw<=l.high,"超出607Dh设备行程");
        (void)MmPerUnit(l.cal);
        const long double gear=static_cast<long double>(l.cal.gear_motor_revolutions)/l.cal.gear_output_revolutions;
        for(auto p:{start,goal,l.first_pos,v.position_raw})
            Require(p*gear>=INT32_MIN&&p*gear<=INT32_MAX,"电子齿轮后位置超出Int32");
        Require(l.profile<=l.max_velocity&&WithinRpm(l.profile,l.cal,l.max_rpm)&&l.accel<=l.max_accel
            &&l.decel<=l.max_decel,"当前速度/加减速超过607F/6080/60C5/60C6设备上限");
        if(stationary) Require(l.first_velocity==0&&v.velocity_raw==0,"实际速度非零，请先独立快停/失能");
        // Halt ignores bit10; actual sampled velocity, NOT target-reached bit,
        // establishes the existing manual stationary policy. No drift rule.
    }
    void Proof(const Live& l,bool stationary) {
        Require(proof,"缺少本client人工目标证明");Validate(l,target,target,stationary);
        Require(Fixed(l,expected),"受保护参数或四项标定已变化");
        Require(l.view.target_raw==target&&l.view.demand_raw==target&&l.view.controlword==proof_word,"607A/6062/6040残留目标或阶段已变化");
        Require(State(l.first_sw)==0x27&&State(l.view.statusword)==0x27,"本会话使能状态已变化");
    }
    void Pause(Clock::time_point until) {
        while(Clock::now()<until) {Check();std::this_thread::sleep_for(std::chrono::milliseconds(1));}Check();
    }
    Live Guard(const Live& fixed,std::int32_t start,std::int32_t goal,bool stationary,
               std::optional<std::uint16_t> cw={},std::optional<std::int32_t> expected_target={},
               std::initializer_list<std::uint16_t> states={}) {
        auto now=Sample();Validate(now,start,goal,stationary);
        Require(Fixed(now,fixed),"动作期间受保护参数/标定已变化");
        if(cw) Require(now.view.controlword==*cw,"6040控制字已变化");
        if(expected_target) Require(now.view.target_raw==*expected_target,"607A冻结目标已变化");
        if(states.size()) for(auto sw:{now.first_sw,now.view.statusword})
            Require(std::find(states.begin(),states.end(),State(sw))!=states.end(),"受控迁移出现非预期状态 "+StateDescription(sw));
        return now;
    }
    Live Transition(std::uint16_t word,std::uint16_t before,std::uint16_t after,const Live& fixed,
                    std::int32_t start,std::int32_t goal,bool stationary,std::optional<bool> ack={},bool demand=false) {
        // One state deadline includes pre-check, download, readback and all polls.
        auto end=std::min(operation_end,Clock::now()+std::chrono::milliseconds(settings.state_timeout_ms));
        struct Restore {Clock::time_point& value;Clock::time_point old;~Restore(){value=old;}} restore{operation_end,operation_end};
        operation_end=end;
        phase="PP受控迁移 "+nimotion::hex(word);
        auto pre=Guard(fixed,start,goal,stationary,{},goal,{before});
        Require(pre.view.controlword==stage_word,"迁移前6040阶段已变化");
        if(stage_ack) Require(bool(pre.first_sw&0x1000)==*stage_ack&&bool(pre.view.statusword&0x1000)==*stage_ack,"迁移前ACK阶段已变化");
        if(word==0x137||word==0x3f) Require(!(pre.first_sw&0x1000)&&!(pre.view.statusword&0x1000),"新ACK触发前已为1");
        Control(word);
        for(;;) {
            Require(Clock::now()<end,"状态迁移/新ACK握手超时");
            // Releasing Halt can immediately move. Only the pre-release
            // snapshot requires zero speed; all later samples allow motion.
            auto now=Guard(fixed,start,goal,stationary&&!(word==0x2f&&before==0x27),word,goal,{before,after});
            bool matched=State(now.first_sw)==after&&State(now.view.statusword)==after;
            if(ack) matched=matched&&bool(now.first_sw&0x1000)==*ack&&bool(now.view.statusword&0x1000)==*ack;
            if(matched) {if(demand) Require(now.view.demand_raw==goal,"6062不是本次人工冻结目标");stage_word=word;stage_ack=ack;
                Emit("受控迁移完成；目标="+std::to_string(goal)+"；set-point ACK="+std::to_string(bool(now.view.statusword&0x1000)));return now;}
            Pause(std::min(end,Clock::now()+std::chrono::milliseconds(settings.poll_interval_ms)));
        }
    }
    Outcome Result(bool ok,const std::string& message,bool disabled=false) {
        last.isolated=bus.Isolated();last.action_locked=locked;last.message=message;
        return {ok,bus.Isolated(),disabled,false,message,last};
    }
    Outcome Stop(bool disable) {
        frozen.reset();
        if(bus.Isolated()) return Result(false,"SDO已隔离；停止/失能不发帧，设备状态未知");
        auto old_ignore=ignore_cancel;auto old_end=operation_end;ignore_cancel=true;
        operation_end=Clock::now()+std::chrono::milliseconds(settings.state_timeout_ms*2+settings.sdo_timeout_ms*8);
        bool disabled=false;
        std::string quick_error;
        try {
            // Explicit stop works without a motion gate, but never without the
            // session's manual OD reason or a live 2002:01 check per controlword.
            phase=disable?"独立快停与失能":"独立快停";
            try{Control(2);}catch(const nimotion::SdoAbort& e){if(!disable||bus.Isolated())throw;quick_error=e.what();}
            proof=false;
            if(disable) Control(0);
            const auto end=operation_end;
            for(;;) {
                auto sw=Read<std::uint16_t>(0x6041);
                auto pos=Read<std::int32_t>(0x6064);auto vel=Read<std::int32_t>(0x606c);
                auto sw2=Read<std::uint16_t>(0x6041);auto vel2=Read<std::int32_t>(0x606c);
                last.statusword=sw2;last.position_raw=pos;last.velocity_raw=vel2;last.state=StateDescription(sw2);
                disabled=State(sw)==0x40&&State(sw2)==0x40;
                const bool quick_stopped=(State(sw)==7||State(sw)==0x40)&&State(sw)==State(sw2);
                if(vel==0&&vel2==0&&(disable?disabled:quick_stopped)) break;
                Require(Clock::now()<end,"停止反馈未确认（失能不等于机械停止）");
                Pause(std::min(end,Clock::now()+std::chrono::milliseconds(settings.poll_interval_ms)));
            }
            if(disabled)touched=false;
            ignore_cancel=old_ignore;operation_end=old_end;
            if(!quick_error.empty())return Result(false,"快停已知拒绝："+quick_error+"；随后独立失能已确认，机械停止未确认",disabled);
            return Result(true,disable ? "已确认失能；机械停止/制动状态未确认" : "已采样速度为零；机械停止未确认",disabled);
        }catch(const std::exception& e){
            locked=true;ignore_cancel=old_ignore;operation_end=old_end;
            const std::string error=quick_error.empty()?e.what():
                "快停已知拒绝："+quick_error+"；失能/反馈失败："+e.what();
            return Result(false,error,disabled);
        }
    }
    bool WarningReset(const Live& l) {
        return State(l.first_sw)==0x40&&State(l.view.statusword)==0x40&&bool(l.first_sw&0x80)
            &&bool(l.view.statusword&0x80)&&!((l.first_sw|l.view.statusword)&(8|0x2000))
            &&l.view.error_code==0x2312&&l.view.error_register==0;
    }
    void ResetHealth(const Live& reference,bool initial,bool finished) {
        auto l=Sample();Validate(l,l.view.position_raw,l.view.position_raw,true,true);
        Emit(initial?"复位初始健康采样":finished?"复位最终低电平健康采样":"复位健康采样");
        Require(Fixed(l,reference),"复位期间受保护参数已变化");
        if(initial) {
            Require(WarningReset(reference)&&WarningReset(l)&&Read<std::uint32_t>(0x200e,2)==0x03002312,"仅允许已核对的失能Warning2312及反应配置");
        } else {
            for(auto sw:{l.first_sw,l.view.statusword}) {
                Require(State(sw)==0x40&&!(sw&(8|0x2000)),"复位期间出现故障/使能/跟随误差");
            }
            Require(l.view.error_code==0||l.view.error_code==reference.view.error_code,"复位期间出现新故障码");
            Require(l.view.error_register==0||l.view.error_register==reference.view.error_register,"复位期间出现新1001h错误");
            if(finished) Require(!((l.first_sw|l.view.statusword)&(8|0x80))&&!l.view.error_code&&!l.view.error_register
                &&l.view.controlword==0,"复位后报警未消除/重新锁存或6040不是0");
        }
    }
};

Controller::Controller(nimotion::CanBus& bus,Settings settings):impl_(std::make_unique<Impl>(bus,std::move(settings))) {}
Controller::~Controller()=default;
bool Controller::Isolated() const noexcept {return impl_->bus.Isolated();}
Snapshot Controller::LastSnapshot() const {auto v=impl_->last;v.isolated=impl_->bus.Isolated();v.action_locked=impl_->locked;return v;}
void Controller::SetDiagnostics(std::function<void(const std::string&)> observer){impl_->diagnostics=std::move(observer);}
void Controller::DiscardReview(){impl_->frozen.reset();}
void Controller::SetCancellationFlag(const std::atomic_bool* cancel){impl_->attached_cancel=cancel;}
Snapshot Controller::Poll() {
    auto& p=*impl_;
    const bool previously_locked=p.locked;
    p.phase="持续保护与实时报警采样";
    p.operation_end=Clock::now()+std::chrono::milliseconds(p.settings.state_timeout_ms);
    try {
        auto l=p.Sample();
        if(p.proof) p.Proof(l,false);
        if((l.first_sw|l.view.statusword)&(8|0x80|0x2000)||l.view.error_code||l.view.error_register) {
            p.reset_allowed=(!p.locked||p.reset_allowed)&&p.WarningReset(l);p.locked=true;
        }
        l.view.action_locked=p.locked;p.last=l.view;p.operation_end=Clock::time_point::max();return l.view;
    } catch(const std::exception& e) {
        const bool cancelled=dynamic_cast<const Cancelled*>(&e)!=nullptr;
        if(!cancelled){p.locked=true;if(!dynamic_cast<const AlarmError*>(&e))p.reset_allowed=false;}
        auto failure=p.last;
        try{p.Emit("首次失败："+std::string(e.what()));}catch(...){}
        if(p.touched&&!p.bus.Isolated()) {auto stopped=p.Stop(true);if(cancelled&&stopped.success)p.locked=previously_locked;}
        p.last=failure;p.last.message=e.what();p.operation_end=Clock::time_point::max();throw;
    }
}
ReviewPlan Controller::Review(Action action,double value) {
    auto& p=*impl_;p.frozen.reset();
    struct Restore {Clock::time_point& value;Clock::time_point old;~Restore(){value=old;}} restore{p.operation_end,p.operation_end};
    p.operation_end=Clock::now()+std::chrono::milliseconds(p.settings.state_timeout_ms);p.Check();
    p.phase="人工动作审阅";
    Require(action==Action::ResetAlarm||!p.locked,"动作已锁定；须处理报警或外部恢复");
    Live l;
    try {
        l=p.Sample();
        if(action!=Action::ResetAlarm) {
            p.Validate(l,l.view.position_raw,l.view.position_raw,true);
            if(p.proof)p.Proof(l,true);
        }
    } catch(const std::exception& e) {
        const bool cancelled=dynamic_cast<const Cancelled*>(&e)!=nullptr;
        if(!cancelled){p.locked=true;if(!dynamic_cast<const AlarmError*>(&e))p.reset_allowed=false;}
        auto failure=p.last;
        try{p.Emit("审阅首次失败："+std::string(e.what()));}catch(...){}
        if(p.touched&&!p.bus.Isolated())p.Stop(true);
        p.last=failure;throw;
    }
    auto& v=l.view;
    ReviewPlan plan;plan.token=++p.serial;plan.action=action;plan.start_raw=v.position_raw;plan.target_raw=v.position_raw;
    plan.requested_rpm=p.settings.rpm;
    Require(std::isfinite(value),"输入必须为有限数值");
    if(action==Action::ResetAlarm) {
        Require(!p.locked||p.reset_allowed,"非Warning2312保护锁禁止通过复位解除");
        p.Validate(l,v.position_raw,v.position_raw,true,true);
        Require(p.WarningReset(l),"仅可人工复位已核对的失能Warning2312；其他Fault不在本入口复位范围");
        p.ResetHealth(l,true,false);
        plan.lines={"人工报警复位："+v.alarm,"6040: 0 → 等100ms → 0080 → 200ms/1s采样 → 0 → 50ms/500ms采样",
                    "仅复位，不使能、不重放、不清历史、不保存；任何不确定立即停止"};
    } else {
        if(Motion(action)) {
            Require(p.proof||((State(l.first_sw)==State(v.statusword))&&(State(v.statusword)==0x40||State(v.statusword)==0x21||State(v.statusword)==0x23)),
                    "外部使能无本会话证明，请先快停/失能");
            auto mm= action==Action::Forward ? p.settings.step_mm : action==Action::Backward ? -p.settings.step_mm : value;
            if(action==Action::Hold)mm=0;
            auto base=action==Action::Absolute?p.zero:v.position_raw;
            auto offset=static_cast<long double>(mm)/p.MmPerUnit(l.cal);
            auto ideal=static_cast<long double>(base)+offset;
            Require(ideal>=l.low&&ideal<=l.high&&ideal>=INT32_MIN&&ideal<=INT32_MAX,"请求目标超出设备607Dh/Int32（量化不能绕过）");
            auto raw=static_cast<std::int64_t>(std::trunc(offset))+base;
            Require(raw>=INT32_MIN&&raw<=INT32_MAX,"目标溢出Int32");plan.target_raw=std::int32_t(raw);
            Require(action==Action::Hold||plan.target_raw!=v.position_raw,"位移不足1用户单位或目标等于当前位置");
            plan.requested_mm=mm;plan.actual_mm=double((raw-base)*p.MmPerUnit(l.cal));
            p.Validate(l,plan.start_raw,plan.target_raw,true);
            plan.profile_velocity=QuantizedUnits(p.settings.rpm,l.cal);plan.actual_rpm=double(ToRpm(plan.profile_velocity,l.cal));
            Require(p.settings.rpm<=l.max_rpm&&plan.profile_velocity<=l.max_velocity&&WithinRpm(plan.profile_velocity,l.cal,l.max_rpm),"请求/量化速度超出6080/607F设备上限");
            plan.lines={action==Action::Hold?"保持当前位置并使能（Halt保持）":"执行一次人工PP位置运动",
                "冻结起点 "+std::to_string(plan.start_raw)+" U → 目标 "+std::to_string(plan.target_raw)+" U",
                "请求 "+Number(mm)+" mm → 实际 "+Number(plan.actual_mm)+" mm",
                "请求 "+Number(plan.requested_rpm)+" rpm → 实际 "+Number(plan.actual_rpm)+" rpm；6081="+std::to_string(plan.profile_velocity)+" U/s（最近整数）",
                "换算 "+Number(p.settings.mm_per_motor_turn)+" mm/电机转；仅设备607D/Int32行程限制",
                "先在Halt下冻结提交，012F保持使能"};
            if(action!=Action::Hold)plan.lines.push_back("使能后重写冻结607A，002F→003F→002F，新ACK 0→1→0；不会自动重复");
        } else {
            Require(action==Action::SetZero||value>0,"设置必须为正数");
            if(action==Action::SetRpm) {
                auto units=QuantizedUnits(value,l.cal);
                Require(units<=l.max_velocity&&value<=l.max_rpm&&WithinRpm(units,l.cal,l.max_rpm),"请求/量化速度超出设备限制");
                plan.requested_rpm=value;plan.profile_velocity=units;plan.actual_rpm=double(ToRpm(plan.profile_velocity,l.cal));
            }
            if(action==Action::SetStep) Require(value/p.MmPerUnit(l.cal)>=1,"步长不足1用户单位");
            plan.lines={action==Action::SetZero?"冻结当前位置为软件零点 "+std::to_string(v.position_raw)+" U；不写607C":
                action==Action::SetRpm?"设置下次运动速度：请求 "+Number(value)+" rpm → 实际 "+Number(plan.actual_rpm)+" rpm（6081最近整数）":
                action==Action::SetStep?"设置点动步长 "+Number(value)+" mm":"设置直线换算 "+Number(value)+" mm/电机转"};
        }
    }
    plan.lines.push_back("再次Enter只执行此冻结动作；软件快停不能替代独立急停");
    p.frozen=Impl::Frozen{plan,l,value};return plan;
}
Outcome Controller::Execute(std::uint64_t token,const std::atomic_bool& cancel) {
    auto& p=*impl_;
    if(!p.frozen||p.frozen->plan.token!=token)return p.Result(false,"审阅已失效/取消/复用");
    auto frozen=std::move(*p.frozen);p.frozen.reset();auto& plan=frozen.plan;
    const bool previously_locked=p.locked;
    bool reset_started=false;
    p.cancel=&cancel;p.operation_end=Clock::now()+std::chrono::milliseconds(p.settings.move_timeout_ms);
    p.phase="冻结动作执行前复核";p.control_attempted=false;p.control_acknowledged=false;
    try {
        p.Check();Require(plan.action==Action::ResetAlarm||!p.locked,"会话动作已锁定");
        auto now=p.Sample();
        Require(Fixed(now,frozen.live)&&now.view.target_raw==frozen.live.view.target_raw&&now.view.demand_raw==frozen.live.view.demand_raw
            &&now.view.controlword==frozen.live.view.controlword&&now.first_sw==frozen.live.first_sw&&now.view.statusword==frozen.live.view.statusword,
            "审阅后配置、目标、控制字或状态变化");
        if(plan.action==Action::ResetAlarm) {
            p.ResetHealth(frozen.live,true,false);
            p.proof=false;p.locked=true;reset_started=true;p.phase="Warning2312人工复位低电平";
            auto reset_control=[&](std::uint16_t word){
                Require(p.Read<std::uint32_t>(0x200e,2)==0x03002312,"复位期间200E:02反应配置已变化");p.Control(word);
            };
            reset_control(0);p.ResetHealth(frozen.live,false,false);
            p.Pause(Clock::now()+std::chrono::milliseconds(100));
            p.phase="Warning2312人工复位高电平";reset_control(0x80);auto high=Clock::now();
            for(int ms:{200,1000}) {p.Pause(high+std::chrono::milliseconds(ms));p.ResetHealth(frozen.live,false,false);}
            p.phase="Warning2312人工复位最终低电平";reset_control(0);auto low=Clock::now();
            for(int ms:{50,500}){p.Pause(low+std::chrono::milliseconds(ms));p.ResetHealth(frozen.live,false,true);}
            p.locked=false;p.reset_allowed=false;p.touched=false;
        } else if(Motion(plan.action)) {
            p.Validate(now,plan.start_raw,plan.target_raw,true);
            if(p.proof)p.Proof(now,true);
            if(p.proof||State(now.view.statusword)!=0x40) {
                auto stopped=p.Stop(true);Require(stopped.success&&stopped.disabled_confirmed,stopped.message);
            }
            p.Check();p.proof=false;
            auto fixed=frozen.live;
            auto initial=p.Guard(fixed,plan.start_raw,plan.target_raw,true,{}, {},{0x40});
            p.stage_word=initial.view.controlword;p.stage_ack.reset();
            Require(initial.view.target_raw==frozen.live.view.target_raw,"失能后残留目标变化");
            p.Write<std::uint32_t>(0x6081,plan.profile_velocity);fixed.profile=plan.profile_velocity;
            p.Guard(fixed,plan.start_raw,plan.target_raw,true,initial.view.controlword,initial.view.target_raw,{0x40});
            p.Write<std::int32_t>(0x607a,plan.target_raw);
            p.Transition(0x106,0x40,0x21,fixed,plan.start_raw,plan.target_raw,true);
            p.Transition(0x107,0x21,0x23,fixed,plan.start_raw,plan.target_raw,true);
            p.Transition(0x127,0x23,0x23,fixed,plan.start_raw,plan.target_raw,true,false);
            p.Transition(0x137,0x23,0x23,fixed,plan.start_raw,plan.target_raw,true,true,true);
            p.Transition(0x127,0x23,0x23,fixed,plan.start_raw,plan.target_raw,true,false,true);
            auto enabled=p.Transition(0x12f,0x23,0x27,fixed,plan.start_raw,plan.target_raw,true,false,true);
            p.target=plan.target_raw;p.expected=fixed;p.proof_word=0x12f;p.proof=true;
            p.Proof(enabled,true);
            if(plan.action!=Action::Hold) {
                p.Write<std::int32_t>(0x607a,plan.target_raw); // never rebase relative target on fresh position
                p.Proof(p.Sample(),true);
                p.Transition(0x2f,0x27,0x27,fixed,plan.start_raw,plan.target_raw,true,false,true);
                p.Transition(0x3f,0x27,0x27,fixed,plan.start_raw,plan.target_raw,false,true,true);
                p.Transition(0x2f,0x27,0x27,fixed,plan.start_raw,plan.target_raw,false,false,true);
                p.proof_word=0x2f;
                for(;;) {
                    auto l=p.Sample();p.Proof(l,false);
                    auto delta=std::abs(std::int64_t(l.view.position_raw)-plan.target_raw);
                    if((l.first_sw&0x400)&&(l.view.statusword&0x400)&&delta<=l.window&&l.first_velocity==0&&l.view.velocity_raw==0)break;
                    p.Pause(Clock::now()+std::chrono::milliseconds(p.settings.poll_interval_ms));
                }
            }
        } else {
            p.Validate(now,now.view.position_raw,now.view.position_raw,true);
            if(p.proof)p.Proof(now,true);
            Require(!p.proof||plan.action==Action::SetZero,"已有保持/运行证明，请先失能再改设置");
            switch(plan.action) {
                case Action::SetZero:p.zero=plan.start_raw;break;
                case Action::SetRpm:p.settings.rpm=frozen.value;break;
                case Action::SetStep:p.settings.step_mm=frozen.value;break;
                case Action::SetMmPerTurn:p.settings.mm_per_motor_turn=frozen.value;break;
                default:throw std::runtime_error("未知动作");
            }
            p.Publish(now);
        }
        p.cancel=nullptr;p.operation_end=Clock::time_point::max();
        return p.Result(true,plan.action==Action::ResetAlarm?"人工复位完成；后续运动须重新审阅":"本次人工动作完成，不会自动重复");
    } catch(const std::exception& e) {
        const std::string first=e.what();const bool cancelled=dynamic_cast<const Cancelled*>(&e)!=nullptr;
        if(!cancelled){p.locked=true;if(!dynamic_cast<const AlarmError*>(&e))p.reset_allowed=false;}
        p.proof=false;
        if(reset_started){p.bus.Isolate("人工复位开始后中断/不确定，禁止尾帧与重试："+first);p.locked=true;p.reset_allowed=false;}
        p.cancel=nullptr;p.operation_end=Clock::time_point::max();
        auto failure=p.last;std::string cleanup;bool disabled=false;
        try{p.Emit("首次失败："+first);}catch(...){}
        // Reset is exactly THREE writes. Any uncertain pulse/health result
        // stops immediately, never appends a speculative low/reset/cleanup.
        if(plan.action!=Action::ResetAlarm&&p.touched&&!p.bus.Isolated()){
            auto stopped=p.Stop(true);cleanup="；清理："+stopped.message;disabled=stopped.disabled_confirmed;
            if(cancelled&&stopped.success)p.locked=previously_locked;
        }
        p.last=failure;return p.Result(false,first+cleanup,disabled);
    }
}
Outcome Controller::SafetyStop(bool disable){return impl_->Stop(disable);}
Outcome Controller::Shutdown(){
    auto& p=*impl_;
    if(p.shutdown_result)return *p.shutdown_result;
    auto result=p.touched?p.Stop(true):p.Result(!p.bus.Isolated(),"会话未发控制下载；关闭零帧");
    p.closed=true;p.frozen.reset();p.shutdown_result=result;return result;
}
} // namespace pmp4010_cpp_test
