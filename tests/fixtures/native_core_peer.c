/* Local protocol peers only. The native MeTTa test driver owns assertions. */
#define _POSIX_C_SOURCE 200809L
#define _DEFAULT_SOURCE
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/file.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
static const char *root, *mode;
static void path(char *out,const char *name){if(snprintf(out,4096,"%s/%s",root,name)>=4096)exit(2);}
static void nap(long ms){struct timespec t={ms/1000,(ms%1000)*1000000};while(nanosleep(&t,&t)&&errno==EINTR){}}
static int sendall(int fd,const char *data,size_t n){while(n){ssize_t w=send(fd,data,n,MSG_NOSIGNAL);if(w<=0)return 0;data+=w;n-=(size_t)w;}return 1;}
static unsigned increment(const char *name){
    char p[4096];path(p,name);int fd=open(p,O_RDWR|O_CREAT,0660);if(fd<0||flock(fd,LOCK_EX))exit(2);
    char b[64]={0};ssize_t n=read(fd,b,sizeof(b)-1);if(n<0)exit(2);unsigned v=(unsigned)strtoul(b,0,10)+1;
    int len=snprintf(b,sizeof(b),"%u\n",v);if(ftruncate(fd,0)||lseek(fd,0,SEEK_SET)<0||write(fd,b,len)!=len)exit(2);
    flock(fd,LOCK_UN);close(fd);return v;
}
static void record(const char *request,unsigned n){
    char name[80],p[4096];snprintf(name,sizeof(name),"request-%u",n);path(p,name);
    FILE *f=fopen(p,"w");if(!f)exit(2);fputs(request,f);fclose(f);
}
static long request_id(const char *s){
    const char *id=strstr(s,"\"id\"");if(!id)return -1;id=strchr(id,':');if(!id)return -1;
    char *end;long value=strtol(id+1,&end,10);return end==id+1?-1:value;
}
static char *reply(const char *request){
    long id=request_id(request);if(id<0)return NULL;
    char result[2048];
    if(strstr(request,"\"initialize\""))
        strcpy(result,"{\"protocolVersion\":\"2025-11-25\",\"capabilities\":{\"tools\":{}},\"serverInfo\":{\"name\":\"native-fixture\",\"version\":\"1\"}}");
    else if(strstr(request,"\"tools/list\""))
        strcpy(result,"{\"tools\":[{\"name\":\"echo\",\"description\":\"Echo locally\\nSecond line\",\"inputSchema\":{\"type\":\"object\"}}]}");
    else if(strstr(request,"\"tools/call\"")){
        unsigned effects=increment("effects");
        snprintf(result,sizeof(result),"{\"content\":[{\"type\":\"text\",\"text\":\"hello\"}],\"isError\":false,\"structuredContent\":{\"effects\":%u,\"sawPrivate\":%s}}",
                 effects,getenv("UNIT_PRIVATE_TOKEN")?"true":"false");
    }else strcpy(result,"{}");
    char *out=malloc(4096);if(!out)exit(2);
    snprintf(out,4096,"{\"jsonrpc\":\"2.0\",\"id\":%ld,\"result\":%s}",id,result);return out;
}
static void http(int fd,int status,const char *type,const char *extra,const char *body){
    char head[1024];size_t n=strlen(body);
    int h=snprintf(head,sizeof(head),"HTTP/1.1 %d Fixture\r\nContent-Type: %s\r\n%sContent-Length: %zu\r\nConnection: close\r\n\r\n",status,type,extra,n);
    if(h>0&&sendall(fd,head,(size_t)h))sendall(fd,body,n);
}
static void append_sse(const char *message){
    char p[4096];path(p,"sse-wire");int fd=open(p,O_WRONLY|O_CREAT|O_APPEND,0660);
    if(fd<0||flock(fd,LOCK_EX))exit(2);
    dprintf(fd,"event: message\ndata: %s\n\n",message);flock(fd,LOCK_UN);close(fd);
}
static void legacy_get(int fd){
    char p[4096];path(p,"sse-wire");FILE *f=fopen(p,"a+");if(!f)return;
    if(fseek(f,0,SEEK_END)){fclose(f);return;}
    const char *head="HTTP/1.1 200 Fixture\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n";
    if(!sendall(fd,head,strlen(head))||!sendall(fd,"event: endpoint\ndata: /messages\n\n",strlen("event: endpoint\ndata: /messages\n\n"))){fclose(f);return;}
    for(int i=0;i<1000;i++){char b[8192];size_t n;while((n=fread(b,1,sizeof(b),f))>0)if(!sendall(fd,b,n)){fclose(f);return;}clearerr(f);nap(20);}
    fclose(f);
}
static void serve_http(int fd){
    struct timeval wait={.tv_sec=5};setsockopt(fd,SOL_SOCKET,SO_RCVTIMEO,&wait,sizeof(wait));
    char *request=calloc(1,1048577);if(!request)exit(2);size_t used=0,length=0;char *end=NULL;
    while(!end&&used<65536){ssize_t n=recv(fd,request+used,65536-used,0);if(n<=0)goto done;used+=(size_t)n;end=strstr(request,"\r\n\r\n");}
    if(!end)goto done;
    size_t header=(size_t)(end-request)+4;
    for(char *line=request;line<end;){char *next=strstr(line,"\r\n");if(!next)break;
        if(!strncasecmp(line,"Content-Length:",15))length=(size_t)strtoul(line+15,0,10);
        line=next+2;}
    if(length>1048576-header)goto done;
    while(used<header+length){ssize_t n=recv(fd,request+used,header+length-used,0);if(n<=0)goto done;used+=(size_t)n;}
    request[used]=0;unsigned number=increment("count");record(request,number);
    if(!strncmp(request,"GET /legacy ",12)){legacy_get(fd);goto done;}
    if(!strncmp(request,"GET /redirect ",14)){http(fd,307,"text/plain","Location: /legacy\r\n","");goto done;}
    const char *body=request+header;
    if(strstr(body,"\"method\":\"notifications/initialized\"")){
        if(!strstr(request,"Mcp-Session-Id: unit-session")&&!strcmp(mode,"http")){
            http(fd,400,"application/json","","{\"error\":\"missing session\"}");goto done;
        }
        http(fd,204,"application/json","","");goto done;
    }
    if(strstr(body,"\"tools/")&&!strcmp(mode,"http")&&!strstr(request,"Mcp-Session-Id: unit-session")){
        http(fd,400,"application/json","","{\"error\":\"missing session\"}");goto done;
    }
    char *response=reply(body);
    if(!response){http(fd,202,"application/json","","");goto done;}
    if(!strcmp(mode,"legacy")){
        append_sse(response);http(fd,202,"application/json","","");
    }else if(!strcmp(mode,"http-sse")){
        const char *head="HTTP/1.1 200 Fixture\r\nContent-Type: text/event-stream\r\nMcp-Session-Id: unit-session\r\nConnection: close\r\n\r\n";
        sendall(fd,head,strlen(head));
        sendall(fd,": keepalive\n\ndata: ",strlen(": keepalive\n\ndata: "));nap(10);
        size_t n=strlen(response),half=n/2;sendall(fd,response,half);nap(10);sendall(fd,response+half,n-half);sendall(fd,"\n\n",2);nap(200);
    }else http(fd,200,"application/json","Mcp-Session-Id: unit-session\r\n",response);
    free(response);
done: free(request);close(fd);
}
static void serve_unix(int fd){
    char request[16384];size_t used=0;
    while(used<sizeof(request)-1){ssize_t n=recv(fd,request+used,sizeof(request)-1-used,0);if(n<=0)break;used+=(size_t)n;if(memchr(request,'\n',used))break;}
    request[used]=0;record(request,increment("count"));
    char p[4096];path(p,"reply");FILE *f=fopen(p,"rb");if(f){int c;while((c=fgetc(f))!=EOF){char one=(char)c;if(!sendall(fd,&one,1))break;nap(1);}fclose(f);}
    close(fd);
}
int main(int argc,char **argv){
    const char *capture=getenv("NATIVE_LAUNCH_CAPTURE");
    int native_launch=0;for(int i=1;i<argc;i++)if(!strcmp(argv[i],"--no-results"))native_launch=1;
    if(capture && native_launch){
        FILE *f=fopen(capture,"w");if(!f)return 2;
        const char *model=getenv("SYNTHETIC_MODEL"),*base=getenv("SYNTHETIC_BASE_URL");
        const char *openai=getenv("OPENAI_BASE_URL"),*key=getenv("SYNTHETIC_API_KEY");
        fprintf(f,"model=%s\nsynthetic_base=%s\nopenai_base=%s\nkey_alias=%d\n",
                model?model:"",base?base:"",openai?openai:"",key&&!strcmp(key,"fixture-omega"));
        fclose(f);return 0;
    }
    if(argc==3 && !strcmp(argv[1],"-p")){
        if(!strcmp(argv[2],"fixture-fail")){fputs("native cli failure\n",stderr);return 3;}
        if(!strcmp(argv[2],"fixture-timeout")){fputs("partial-output",stdout);fflush(stdout);nap(1000);return 0;}
        puts("native cli answer");return 0;
    }
    if(argc!=3){fputs("native-core-peer MODE PRIVATE_DIRECTORY\n",stderr);return 2;}
    mode=argv[1];root=argv[2];signal(SIGPIPE,SIG_IGN);
    if(!strcmp(mode,"stdio")){
        char *line=NULL;size_t cap=0;while(getline(&line,&cap,stdin)>0){
            record(line,increment("count"));char *response=reply(line);
            if(response){printf("%s\n",response);fflush(stdout);free(response);}}
        free(line);return 0;
    }
    signal(SIGCHLD,SIG_IGN);
    int unix_mode=!strcmp(mode,"unix"),server=socket(unix_mode?AF_UNIX:AF_INET,SOCK_STREAM,0);if(server<0)return 2;
    if(unix_mode){char p[4096];path(p,"socket");struct sockaddr_un a={.sun_family=AF_UNIX};if(strlen(p)>=sizeof(a.sun_path))return 2;
        strcpy(a.sun_path,p);if(bind(server,(struct sockaddr *)&a,sizeof(a)))return 2;
    }else{struct sockaddr_in a={.sin_family=AF_INET,.sin_addr.s_addr=htonl(INADDR_LOOPBACK),.sin_port=0};
        if(bind(server,(struct sockaddr *)&a,sizeof(a)))return 2;
        socklen_t size=sizeof(a);
        if(getsockname(server,(struct sockaddr *)&a,&size))return 2;
        char p[4096];path(p,"port");FILE *f=fopen(p,"w");if(!f)return 2;
        fprintf(f,"%u\n",ntohs(a.sin_port));fclose(f);}
    if(listen(server,16))return 2;
    for(;;){int fd=accept(server,0,0);if(fd<0){if(errno==EINTR)continue;return 2;}
        pid_t child=fork();if(child<0)return 2;if(!child){close(server);if(unix_mode)serve_unix(fd);else serve_http(fd);_exit(0);}close(fd);}
}
