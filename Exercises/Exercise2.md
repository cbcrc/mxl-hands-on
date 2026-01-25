## Excercise 2 - Multiple writers and multiple domains

### Synopsis
Building on the foundational concepts from Exercise 1, this exercise will demonstrate how MXL handles **multiple concurrent video flows** and the concept of **domain separation**.

You will deploy three Docker containers: two MXL writers, each generating a unique video flow, and one MXL reader. We will explore how the reader interacts with multiple flows on the **same MXL domain**, observe the resulting file structure, and then **modify a writer's domain** to understand how flows can be isolated. This hands-on experience will solidify your understanding of MXL's domain-based organization.

```mermaid
   graph
      direction LR
         subgraph Node/Host
            subgraph docker_writer_1 [docker]
                  direction LR
                  gstreamer_writer_1[Gstreamer writer]
                  mxl_sdk_writer_1[MXL SDK]
                  gstreamer_writer_1 --> mxl_sdk_writer_1
            end
             
             subgraph docker_writer_2 [docker]
                  direction LR
                  gstreamer_writer_2[Gstreamer writer]
                  mxl_sdk_writer_2[MXL SDK]
                  gstreamer_writer_2 --> mxl_sdk_writer_2
            end

            subgraph docker_reader [docker]
                  direction LR
                  gstreamer_reader[Gstreamer Reader]
                  mxl_sdk_reader[MXL SDK]
                  mxl_sdk_reader --> gstreamer_reader
            end

            tmpfs([tmpfs<br>/Volumes/mxl/domain_1])

            tmpfs2([tmpfs<br>/Volumes/mxl/domain_2])

            mxl_sdk_writer_1 --> tmpfs
            mxl_sdk_writer_2 --> tmpfs
            mxl_sdk_writer_2 -.-> tmpfs2
            tmpfs --> mxl_sdk_reader
         end

         %% Styling
         linkStyle default stroke:black,stroke-width:2px
         style docker_writer_1 fill:#cce6ff,color:black,stroke:#333,stroke-width:3px
         style docker_writer_2 fill:#cce6ff,color:black,stroke:#333,stroke-width:3px
         style docker_reader fill:#cce6ff,color:black,stroke:#333,stroke-width:3px
         style gstreamer_writer_1 fill:#66b3ff,color:black,stroke:#333,stroke-width:2px
         style gstreamer_writer_2 fill:#66b3ff,color:black,stroke:#333,stroke-width:2px
         style gstreamer_reader fill:#66b3ff,color:black,stroke:#333,stroke-width:2px
         style mxl_sdk_writer_1 fill:#007bff,color:black,stroke:#333,stroke-width:2px,color:#fff
         style mxl_sdk_writer_2 fill:#007bff,color:black,stroke:#333,stroke-width:2px,color:#fff
         style mxl_sdk_reader fill:#007bff,color:black,stroke:#333,stroke-width:2px,color:#fff
         style tmpfs fill:#ffe0b3,color:black,stroke:#333,stroke-width:2px
         style tmpfs2 fill:#ffe0b3,color:black,stroke:#333,stroke-width:2px
```

### Setps

1. Go to exercise 2 folder  
   ```sh
   cd ~/mxl-hands-on/docker/exercise-2
   ```
1. Look at the docker-compose.yaml file and notice that we now have 2 writers and that all containers are mapped to the same MXL domain.  
   ```sh
   cat docker-compose.yaml
   ```
1. Start the containers with the provided .yaml file  
   ```sh
   docker compose up -d
   ```
1. Look at the containers running  
   ```sh
   docker container ls
   ```
1. Look at the MXL domain file structure as seen by the reader app. Notice the second flow with a new unique ID  
   ```sh
   docker exec exercise-2-reader-media-function-1 ls /domain
   ```
1. Store the second video Flow ID into a local variable called **FLOW2V_ID**. From what we observed in the docker-compose.yaml file, can you explain why we have only 3 flows in our MXL domain?
   ```sh
   FLOW2V_ID=93abcf83-c7e8-41b5-a388-fe0f511abc12
   ```
1. Look at the MXL domain_1 file structure on the host.  
   ```sh
   ls /Volumes/mxl/domain_1
   ```
1. Use mxl-info to get flow information from the mxl reader to get a list of all flow available in the domain
   ```sh
   docker exec exercise-2-reader-media-function-1 /app/mxl-info -d /domain -l
   ```
1. Shut down the containers of exercise 2  
   ```sh
   docker compose down
   ```
1. Look at the other docker-compose.yaml located in the /data folder and look for the difference in between the two.
   ```sh
   cat ./data/docker-compose.yaml
   ```
1. Replace the docker-compose.yaml with the new docker-compose.yaml from the /data folder.
   ```sh
   sudo cp ./data/docker-compose.yaml .
   ```
1. Create a second mxl domain in the mxl tmpfs drive.
   ```sh
   mkdir /Volumes/mxl/domain_2
   ```
1. Copy the domain configuration file into domain 2 and look at it.
   ```sh
   sudo cp ./data/options.json /Volumes/mxl/domain_2
   cat /Volumes/mxl/domain_2/options.json
   ```
1. Start up the containers with the updated .yaml file  
   ```sh
   docker compose up -d
   ```
1. Look at the MXL domain file structure as seen by the reader app. Notice that we only see the flow of the first writer app after we change the domain of writer 2.  
   ```sh
   docker exec exercise-2-reader-media-function-1 ls /domain
   ```
1. Look at the MXL domain_1 and domain_2 file structure on the host and notice that both flows still exist but they are isolated by their MXL domain.  
   ```sh
   ls /Volumes/mxl/domain_1 && ls /Volumes/mxl/domain_2
   ```
1. Look at the grain count for flows in domain 2. The change is the result of the applied options.json file to domain 2.
   ```sh
   ls /Volumes/mxl/domain_2/$FLOW2V_ID.mxl-flow/grains
   ```
1. Shutdown containers of exercise 2  
   ```sh
   docker compose down
   ```


### Extra information exercise 2
This exercise expands on the foundational concepts introduced in Exercise 1 by demonstrating how MXL handles multiple media flows. Understanding how these flows coexist and how domains can provide isolation. We also saw the domain options configuration file that is defining the depth of the mxl buffers for the domain.

#### Coexistence of Multiple Flows within a Single Domain
In the initial setup of Exercise 2 (Steps 2 through 7), you observed two MXL writers contributing distinct video flows to the same MXL domain (`Volumes/mxl/domain_1`).  

* Unique Flow Identification: Even though both flows share the same root domain, MXL maintains strict separation and identification of each flow. This is achieved through:
	* Unique `flowIds`: As you observed in Step 5 (l`s /domain`), each flow gets its own distinct `flowId` (a UUID), which serves as its unique identifier within the domain.
	* Dedicated Flow Directories: Each `flowId` corresponds to its own dedicated directory (`<flowId>.mxl-flow`) within the domain's file structure. This ensures that the flow definition (e.g., `flow_def.json`) and the actual media grains for one flow are completely separate from another.
* `mxl-info -l` for Domain-Wide Overview: Step 7 introduces the `mxl-info -l` command. The `-l` (list) flag is useful; it instructs mxl-info to scan the specified MXL domain and list all active flows within it. 

#### The Power of MXL Domains for Isolation
The core learning objective of the latter part of Exercise 2 (Steps 8 through 12) is to understand the concept of domain separation in MXL.

* **Logical and Physical Isolation:** By modifying the `docker-compose.yaml` file to map writer-2 to `/Volumes/mxl/domain_2`, you effectively writing to a second, entirely separate MXL domain.
	* **Logical Isolation:** From the perspective of applications, a flow existing in `domain_1` is completely distinct and inaccessible to an application configured only to read from `domain_2`, and vice-versa.
	* **Physical Isolation:** As you confirmed in Step 12 (`ls /Volumes/mxl/domain_1` and `ls /Volumes/mxl/domain_2`), the two domains exist as independent directory structures on the host's `tmpfs` filesystem.
* **Use Cases for Multiple Domains:** The ability to establish multiple, isolated MXL domains is a fundamental feature for various architectural patterns:
	* **Security:** Different applications or user groups can be granted access only to specific domains, ensuring that sensitive media flows are isolated from less secure ones.
	* **Workload Separation:** High-sensitivity workflows, like playout, can be isolated in their own domain to prevent interference from other, less critical workflows, ensuring consistent operation of critical systems.
	* **Organizational Boundaries:** In large media systems, different production groups might operate their own distinct MXL domains to manage their media assets independently.
	* **Resource Management:** While tmpfs uses shared memory, creating separate domains can simplify resource allocation and monitoring for distinct sets of flows.
	* **Scalability:** While not directly demonstrated in this exercise, the concept of domains facilitates distributed architectures where different parts of a system might manage their own local MXL domains.

**Key Takeaway:** The concept of domains in MXL allows you to set up clear boundaries for your media workflows. Whether for security, isolation, organizational purposes, or robust resource management, multiple MXL domains provide the necessary separation and control for complex media exchange environments.
### [Back to main page](../README.md)