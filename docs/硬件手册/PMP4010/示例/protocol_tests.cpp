// Portable offline protocol tests; no physical CAN interface required.
#include "canopen_servo.hpp"
#include <deque>
#include <iostream>
#include <map>
#include <vector>
using namespace nimotion;
static void check(bool ok, const char* why) { if (!ok) throw std::runtime_error(why); }
template<class E, class Fn> static void throws(Fn fn) {
    bool caught = false;
    try { fn(); } catch (const E&) { caught = true; }
    check(caught, "Expected exception was not raised");
}
struct Object { uint32_t value; unsigned width; };
class FakeDrive : public CanBus {
public:
    std::map<uint32_t, Object> objects;
    std::deque<Frame> replies;
    std::vector<Frame> sent;
    bool silent = false, positionMoves = true, acceptSetpoint = true, staleReached = false;
    bool injectUnrelated = false;
    uint32_t forcedAbort = 0;
    uint16_t controlword = 0;
    uint16_t currentState = 0x40;
    int32_t actualPosition = 0;
    int motionPolls = 0;
    int resetPolls = 0;
    bool acknowledged = false;
    unsigned feeds = 0;
    uint32_t key(uint16_t index, uint8_t sub) { return uint32_t(index) << 8 | sub; }
    void object(uint16_t index, uint8_t sub, uint32_t v, unsigned n) { objects[key(index, sub)] = {v, n}; }
    uint32_t value(uint16_t index, uint8_t sub = 0) { return objects.at(key(index, sub)).value; }
    FakeDrive() {
        object(0x6041,0,0x40,2); object(0x6040,0,0,2); object(0x2002,1,4,2);
        object(0x6060,0,1,1); object(0x6061,0,1,1);
        object(0x608f,1,10000,4); object(0x608f,2,1,4);
        object(0x6091,1,1,4); object(0x6091,2,1,4);
        object(0x607d,1,uint32_t(-65535),4); object(0x607d,2,65535,4);
        object(0x607f,0,500000,4); object(0x60c5,0,500000,4); object(0x60c6,0,500000,4);
        object(0x6083,0,409600,4); object(0x6084,0,409600,4); object(0x6081,0,500000,4);
        object(0x6082,0,0,4); object(0x605d,0,1,2); object(0x605a,0,2,2);
        object(0x607a,0,0,4); object(0x6064,0,0,4); object(0x6067,0,10,4);
        object(0x60ff,0,99999,4); object(0x606c,0,0,4); object(0x2006,0x12,0,2);
        object(0x603f,0,0,2);
        object(0x200c,2,1,2); object(0x200c,0x1b,3000,2); object(0x1010,1,1,4);
    }
    void send(const Frame& f) override {
        sent.push_back(f);
        if (f.id == 0) { check(f.size == 2 && f.data[1] == 1, "NMT must target node 1"); return; }
        check(f.id == 0x601 && f.size == 8, "SDO COB-ID/DLC");
        if (silent) return;
        uint16_t index = uint16_t(decode(f.data.data()+1,2)); uint8_t sub = f.data[3];
        Frame r; r.id = 0x581; r.size = 8;
        r.data[1]=f.data[1]; r.data[2]=f.data[2]; r.data[3]=sub;
        auto it = objects.find(key(index,sub));
        uint32_t abort = forcedAbort;
        if (it == objects.end()) abort = 0x06020000;
        if (!abort && f.data[0] != 0x40 && 4-((f.data[0]>>2)&3) != it->second.width)
            abort = 0x06070010;
        if (abort) { r.data[0]=0x80; encode(r.data.data()+4,abort,4); }
        else if (f.data[0] == 0x40) {
            if (index == 0x6041) {
                if (resetPolls > 0 && --resetPolls == 0) currentState = 0x40;
                if (motionPolls > 0 && --motionPolls == 0 && positionMoves) {
                    actualPosition = int32_t(value(0x607a));
                }
                bool reached = actualPosition == int32_t(value(0x607a)) || staleReached;
                it->second.value = currentState | (acknowledged ? 0x1000 : 0) | (reached ? 0x400 : 0);
            }
            if (index == 0x6064) it->second.value = uint32_t(actualPosition);
            r.data[0]=uint8_t(0x43 | ((4-it->second.width)<<2));
            encode(r.data.data()+4,it->second.value,it->second.width);
        } else {
            it->second.value = decode(f.data.data()+4,it->second.width); r.data[0] = 0x60;
            if (index == 0x200c && sub == 0x1b) ++feeds;
            if (index == 0x1010 && sub == 1) it->second.value = 1;
            if (index == 0x6060) object(0x6061,0,it->second.value,1);
            if (index == 0x6040) {
                uint16_t cw = uint16_t(it->second.value);
                if (cw == 0x80 && (currentState & 8)) resetPolls = 3;
                else if (cw == 6) currentState = 0x21;
                else if (cw == 7) currentState = 0x23;
                else if (cw == 2) currentState = 0x40;
                else if ((cw & 15) == 15) currentState = 0x27;
                if (value(0x6061) == 1) {
                    if (!(cw & 0x10)) acknowledged = false;
                    if ((cw & 0x10) && !(controlword & 0x10) && acceptSetpoint) {
                        acknowledged = true; motionPolls = 5;
                    }
                }
                if (cw & 0x100) object(0x606c,0,0,4);
                controlword = cw;
            }
        }
        if (injectUnrelated) {
            Frame other = r; other.id = 0x582; replies.push_back(other);
            other = r; other.data[3] = 0xff; replies.push_back(other);
            other = r; other.size = 7; replies.push_back(other);
        }
        replies.push_back(r);
    }
    bool receive(Frame& f, int) override {
        if (replies.empty()) return false;
        f = replies.front(); replies.pop_front(); return true;
    }
};
int main() {
    try {
        {
            FakeDrive bus; bus.injectUnrelated = true; SdoClient s(bus);
            bus.actualPosition=-123456;
            check(s.read<int32_t>(0x6064)==-123456, "Signed little-endian read");
            s.write<int32_t>(0x60ff,0,-50000);
            const auto& f=bus.sent.back();
            check(f.data == std::array<uint8_t,8>{0x23,0xff,0x60,0,0xb0,0x3c,0xff,0xff}, "PV negative frame");
            s.write<uint16_t>(0x2002,1,0);
            check(bus.sent.back().data[0]==0x2b, "2002:01 must be uint16");
            throws<std::runtime_error>([&]{s.read<uint8_t>(0x2002,1);});
            bus.forcedAbort=0x06010002;
            throws<SdoAbort>([&]{s.write<uint16_t>(0x6040,0,6);});
        }
        {
            FakeDrive bus; bus.silent=true; SdoClient s(bus,1,2);
            throws<std::runtime_error>([&]{s.read<uint16_t>(0x6041);});
            auto n=bus.sent.size(); bus.silent=false;
            throws<std::runtime_error>([&]{s.read<uint16_t>(0x6041);});
            check(bus.sent.size()==n, "No retry after timeout");
            s.bestEffortQuickStop(); check(bus.sent.back().data[4]==2, "Stop attempt after timeout");
        }
        {
            Units u{10000,1,1};
            check(u.position(360)==10000 && u.position(-180)==-5000, "Position conversion");
            check(u.motorRpm(-300)==-50000, "PV target uses UU/s not rpm");
            check(u.feedbackRpm(50000,1)==300 && u.feedbackRpm(300,0)==300, "Selectable feedback units");
            u.electronicGear=36; u.mechanicalReduction=36;
            check(u.position(360)==10000, "Mechanical reduction and electronic gear distinct");
            throws<std::out_of_range>([&]{u.position(1e20);});
            throws<std::invalid_argument>([&]{u.speed(0);});
            throws<std::invalid_argument>([&]{u.position(std::numeric_limits<double>::quiet_NaN());});
        }
        {
            FakeDrive bus; Servo m(bus);
            check(bus.sent.empty(), "Construction must not send commands");
            m.configure(1); check(bus.currentState==0x23, "Configuration must remain disabled");
            m.enable(); check(bus.currentState==0x27, "Enable state sequence");
            m.moveAbsolute(90,36,500);
            check(bus.actualPosition==2500 && bus.value(0x6081)==1000, "PP reached actual target");
            std::vector<uint16_t> edges;
            for (auto& f:bus.sent) if(f.id==0x601 && f.data[0]==0x2b && f.data[1]==0x40 && f.data[2]==0x60)
                edges.push_back(uint16_t(decode(f.data.data()+4,2)));
            check(edges.size()>=3 && edges[edges.size()-3]==0x2f && edges[edges.size()-2]==0x3f && edges.back()==0x2f, "PP handshake edges");
            m.moveRelative(-36,36,500); check(bus.actualPosition==1500, "Relative starts at actual position");
            auto n=bus.sent.size();
            throws<std::out_of_range>([&]{m.moveAbsolute(1e8,36);});
            check(bus.sent.size()==n, "Reject overflow before transmission");
            throws<std::out_of_range>([&]{m.moveAbsolute(3000,36);});
            m.disable(); m.configure(3); m.enable();
            check(bus.value(0x60ff)==0, "Stale PV target cleared before enable");
            m.setMotorRpm(-300); check(bus.value(0x60ff)==uint32_t(-50000), "Negative PV target");
            m.halt(); check(bus.value(0x60ff)==0 && (bus.controlword&0x100), "Halt clears velocity and stays halted");
            m.setMotorRpm(60); check(bus.value(0x60ff)==10000 && bus.controlword==15, "Resume writes fresh target");
            m.shutdownNoThrow(); check(bus.currentState==0x23, "Exit disables after halt");
        }
        {
            FakeDrive bus; Servo m(bus); m.configure(1); m.enable();
            bus.positionMoves=false; bus.staleReached=true;
            throws<std::runtime_error>([&]{m.moveAbsolute(90,36,20);});
            check(bus.actualPosition==0, "Stale target-reached cannot report success");
        }
        {
            FakeDrive bus; bus.currentState=8; Servo m(bus);
            throws<std::runtime_error>([&]{m.configure(1);});
            check(bus.value(0x2002,1)==4, "Fault must not auto-reset or configure");
            m.resetFault(); check(bus.currentState==0x40, "Explicit fault reset");
        }
        {
            FakeDrive bus; Servo m(bus);
            m.changeNodeId(2);
            check(bus.value(0x200c,2)==2, "ID written to 200C:02");
            bool saved=false;
            for (const auto& f:bus.sent) {
                if (f.id==0x601 && f.data==std::array<uint8_t,8>{0x23,0x10,0x10,1,0x73,0x61,0x76,0x65}) saved=true;
            }
            check(saved, "ID change saves using 1010:01 signature");
            auto n=bus.sent.size();
            throws<std::invalid_argument>([&]{m.changeNodeId(0);});
            throws<std::invalid_argument>([&]{m.changeNodeId(128);});
            check(n==bus.sent.size(), "Invalid ID must not send commands");
            bus.currentState=0x27;
            throws<std::runtime_error>([&]{m.changeNodeId(3);});
            check(bus.value(0x200c,2)==2, "Enabled drive must not change ID");
        }
        {
            FakeDrive bus; bus.object(0x200c,0x1b,1000,2); Servo m(bus);
            m.configure(1); check(bus.feeds>=1, "Configuration feeds existing watchdog");
            auto n=bus.feeds;
            std::this_thread::sleep_for(std::chrono::milliseconds(350));
            m.pollHealth(); check(bus.feeds==n+1, "Idle health polling must feed watchdog");
            check(bus.value(0x200c,0x1b)==1000, "Watchdog interval preserved");
        }
        {
            FakeDrive bus; bus.object(0x200c,0x1b,0,2); Servo m(bus);
            m.configure(1); m.pollHealth(); check(bus.feeds==0, "Disabled watchdog stays disabled");
        }
        {
            FakeDrive bus; bus.objects.erase(bus.key(0x200c,0x1b)); Servo m(bus);
            m.configure(1); m.enable(); check(bus.currentState==0x27, "Older firmware without watchdog object");
        }
        {
            FakeDrive bus; SdoClient s(bus);
            Frame emcy; emcy.id=0x81; emcy.size=8; emcy.data[0]=0x10; emcy.data[1]=0x23;
            bus.replies.push_back(emcy);
            throws<std::runtime_error>([&]{s.read<uint16_t>(0x6041);});
            check(bus.sent.empty(), "Queued emergency prevents new SDO request");
        }
        std::cout << "PASS: SDO, units, PP/PV, limits, arrival, stop, faults, EMCY, node ID persistence, watchdog feeds\n";
        return 0;
    } catch (const std::exception& e) { std::cerr << "FAIL: " << e.what() << '\n'; return 1; }
}
