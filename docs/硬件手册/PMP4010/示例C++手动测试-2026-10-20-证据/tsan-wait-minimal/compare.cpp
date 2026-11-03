#include <chrono>
#include <condition_variable>
#include <mutex>
#include <thread>
#include <cstdio>
#include <cstring>
#include <pthread.h>
#include <time.h>
int main(int argc,char** argv){
 const bool unsafe=argc>1&&std::strcmp(argv[1],"unsafe")==0;
 const bool direct=unsafe||(argc>1&&std::strcmp(argv[1],"pthread")==0);
 const bool wall=argc>1&&std::strcmp(argv[1],"system")==0;
 std::mutex mutex;std::condition_variable condition;int shared=0;
 pthread_condattr_t attr;pthread_condattr_init(&attr);pthread_condattr_setclock(&attr,CLOCK_MONOTONIC);
 pthread_cond_t native;pthread_cond_init(&native,&attr);pthread_condattr_destroy(&attr);
 std::thread worker([&]{for(int i=0;i<100;++i){std::unique_lock<std::mutex> lock(mutex);
  if(direct){timespec end{};clock_gettime(CLOCK_MONOTONIC,&end);end.tv_nsec+=1000000;if(end.tv_nsec>=1000000000){++end.tv_sec;end.tv_nsec-=1000000000;}pthread_cond_timedwait(&native,mutex.native_handle(),&end);}
  else if(wall)condition.wait_until(lock,std::chrono::system_clock::now()+std::chrono::milliseconds(1));
  else condition.wait_until(lock,std::chrono::steady_clock::now()+std::chrono::milliseconds(1));
  ++shared;
 }});
 int observed=0;for(int i=0;i<100;++i){if(unsafe)observed=shared;else{std::lock_guard<std::mutex> lock(mutex);observed=shared;}std::this_thread::sleep_for(std::chrono::milliseconds(1));}
 worker.join();pthread_cond_destroy(&native);std::printf("mode=%s final=%d observed=%d\n",argc>1?argv[1]:"steady",shared,observed);
}
